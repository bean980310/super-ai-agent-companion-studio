# MCP Tool-Calling Agent
# MCP 도구를 모델 실행 계층(src.models.models)에 연결하는 어댑터.
# 클라이언트 생성/캐시/샘플링 설정은 models.generate_answer가 담당하고,
# 이 모듈은 MCP 도구 선택·스키마 변환·실행기 구성만 처리한다.

from typing import Any, Dict, List, Optional

from ai_companion_gateway import ToolResult, build_tool_specs_from_mcp, supports_openai_tools

from src.common.custom_providers import CUSTOM_PREFIX
from src.models.models import DEFAULT_MAX_TOOL_ITERATIONS, generate_answer

from .client.manager import MCPClientManager, get_mcp_client_manager
from .client.models import MCPToolResult
from .runtime import run_mcp_coro

MAX_TOOL_ITERATIONS = DEFAULT_MAX_TOOL_ITERATIONS

_TOOL_USAGE_HINT = (
    "You can call external tools to answer the user. "
    "Use the provided functions when they help fulfill the request. "
    "After receiving tool results, produce the final answer in the user's language."
)


def supports_mcp_tools(provider: str) -> bool:
    """Whether the given provider can drive MCP tool calling.

    True for ``custom:*`` profiles and providers the gateway registers as
    OpenAI-compatible endpoints.
    """
    if not provider:
        return False
    if provider.startswith(CUSTOM_PREFIX):
        return True
    return supports_openai_tools(provider)


def _mcp_result_to_tool_result(result: MCPToolResult) -> ToolResult:
    return ToolResult(
        success=result.success,
        content=result.content,
        error=result.error,
        content_type=result.content_type,
    )


def _make_executor(manager: MCPClientManager):
    def executor(tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        result = run_mcp_coro(manager.call_tool(tool_name, arguments))
        return _mcp_result_to_tool_result(result)

    return executor


def _select_tools(manager: MCPClientManager, tool_names: Optional[List[str]]) -> List[Any]:
    all_tools = manager.list_tools()
    if tool_names:
        selected = set(tool_names)
        all_tools = [t for t in all_tools if t.full_name in selected or t.name in selected]
    return all_tools


def _inject_tool_hint(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """시스템 메시지에 tool 사용 힌트를 추가한다 (원본은 변경하지 않음)."""
    history = [dict(msg) for msg in history]
    if history and history[0].get("role") == "system":
        content = history[0].get("content", "")
        if isinstance(content, list):
            content = list(content) + [{"type": "text", "text": _TOOL_USAGE_HINT}]
        else:
            content = f"{content}\n\n{_TOOL_USAGE_HINT}" if content else _TOOL_USAGE_HINT
        history[0]["content"] = content
    else:
        history.insert(0, {"role": "system", "content": _TOOL_USAGE_HINT})
    return history


def run_tool_agent(
    history: List[Dict[str, Any]],
    selected_model: str,
    provider: str,
    api_key: Optional[str] = None,
    tool_names: Optional[List[str]] = None,
    model_type: Optional[str] = None,
    selected_lora: Optional[str] = None,
    local_model_path: Optional[str] = None,
    lora_path: Optional[str] = None,
    image_input: Any = None,
    audio_input: Any = None,
    video_input: Any = None,
    device: str = "cpu",
    seed: int = 42,
    max_length: int = -1,
    temperature: float = 1.0,
    top_k: int = 50,
    top_p: float = 1.0,
    repetition_penalty: float = 1.0,
    enable_thinking: bool = False,
    enable_streaming: bool = False,
    character_language: str = "ko",
    max_iterations: int = MAX_TOOL_ITERATIONS,
    manager: Optional[MCPClientManager] = None,
) -> str:
    """
    Run the model with MCP tools.

    ``models.generate_answer``와 동일한 파라미터를 받아, MCP 도구를
    tool_specs/tool_executor로 붙여 실행한다. 모델 로드·캐시·샘플링 설정은
    models 계층이 단일 책임으로 처리한다.

    Args:
        history: Conversation history in the app's internal message format.
        selected_model: Model id sent to the provider.
        provider: LLM provider id (must be tool-capable).
        api_key: Optional API key; falls back to env / stored profile.
        tool_names: Optional list of MCP tool full names to expose. When None,
            all discovered tools are exposed.
        model_type: Local model type (self-provided only).
        selected_lora: LoRA id for local models.
        local_model_path: Custom local model path.
        lora_path: LoRA path.
        image_input: Optional image input.
        audio_input: Optional audio input.
        video_input: Optional video input.
        device: Local inference device.
        seed: Sampling seed.
        max_length: Max generation length (``<= 0`` uses the model default).
        temperature: Sampling temperature.
        top_k: Top-k sampling.
        top_p: Nucleus sampling.
        repetition_penalty: Repetition penalty.
        enable_thinking: Enable reasoning/thinking where supported.
        enable_streaming: Stream tokens to stdout.
        character_language: Output language hint.
        max_iterations: Safety cap on tool-calling rounds.
        manager: Optional MCP client manager (defaults to the global one).

    Returns:
        The assistant's final text answer (already stripped).
    """
    manager = manager or get_mcp_client_manager()

    tools = _select_tools(manager, tool_names)
    if not tools:
        raise RuntimeError("No MCP tools are available. Connect an MCP server and select tools.")

    tool_specs = build_tool_specs_from_mcp(tools)

    return generate_answer(
        history=_inject_tool_hint(history),
        selected_model=selected_model,
        provider=provider,
        model_type=model_type,
        selected_lora=selected_lora,
        local_model_path=local_model_path,
        lora_path=lora_path,
        image_input=image_input,
        audio_input=audio_input,
        video_input=video_input,
        api_key=api_key,
        device=device,
        seed=seed,
        max_length=max_length,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
        enable_thinking=enable_thinking,
        enable_streaming=enable_streaming,
        tool_specs=tool_specs,
        tool_executor=_make_executor(manager),
        max_tool_iterations=max_iterations,
        character_language=character_language,
    )


def list_selectable_mcp_tools() -> List[Dict[str, str]]:
    """Return connected MCP tools as simple dicts for UI dropdowns."""
    manager = get_mcp_client_manager()
    return [
        {
            "value": t.full_name,
            "label": f"{t.server_name}: {t.name}",
            "description": t.description or "",
        }
        for t in manager.list_tools()
    ]
