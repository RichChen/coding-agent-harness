"""Current-workspace retrieval and host-frozen public validation tools."""
from src.tools.base import Tool, ToolResult, ToolStatus, SideEffectClass
from src.tools.retrieve import RetrieveCodeTool
from src.workspace.control import CaptureError


class WorkspaceRetrieveTool(Tool):
    name = "retrieve_code"
    description = "Search the current captured Python workspace with BM25. Results carry capture/candidate/corpus/index versions and current file SHA/locations."
    parameters = RetrieveCodeTool.parameters
    side_effect = SideEffectClass.READ_ONLY

    def __init__(self, controller): self.controller = controller

    async def execute(self, params, ctx):
        try:
            value = await self.controller.retrieve(params["query"], params.get("k", 5))
            return ToolResult(ctx.call_id, self.name, value.get("status") == "ready",
                              self.controller.observation(value))
        except CaptureError as exc:
            return ToolResult(ctx.call_id, self.name, False, "", error=exc.reason,
                              status=ToolStatus.CANCELLED if exc.reason=="cancelled" else ToolStatus.FAILED,
                              error_kind="user_cancelled" if exc.reason=="cancelled" else exc.reason)


class ValidatePatchTool(Tool):
    name = "validate_patch"
    description = "Run the host-frozen public regression checks on the current candidate in a fresh replica. Test status is distinct from tool success and does not certify the issue solved."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    side_effect = SideEffectClass.NON_IDEMPOTENT

    def __init__(self, controller): self.controller = controller

    async def execute(self, params, ctx):
        try:
            value = await self.controller.validate_tool()
            effective = value["status"] in ("passed", "behavior_failed")
            reason = value["reason"]
            status = (ToolStatus.SUCCEEDED if effective else ToolStatus.CANCELLED if reason=="cancelled" else
                      ToolStatus.TIMED_OUT if reason in ("timeout", "deadline") else ToolStatus.FAILED)
            return ToolResult(ctx.call_id, self.name, effective, self.controller.observation(value),
                              status=status, error_kind=None if effective else reason)
        except CaptureError as exc:
            return ToolResult(ctx.call_id, self.name, False, "", error=exc.reason,
                              status=ToolStatus.CANCELLED if exc.reason=="cancelled" else ToolStatus.FAILED,
                              error_kind="user_cancelled" if exc.reason=="cancelled" else exc.reason)
