# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.debate_utils import safe_int


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    @staticmethod
    def _should_continue_tool_call(state: AgentState) -> str:
        """Generic: continue if last message has tool calls."""
        messages = state.get("messages") or []
        if not messages:
            return "done"
        last_message = messages[-1]
        return "continue" if getattr(last_message, "tool_calls", None) else "done"

    should_continue_market = _should_continue_tool_call
    should_continue_social = _should_continue_tool_call
    should_continue_news = _should_continue_tool_call
    should_continue_fundamentals = _should_continue_tool_call
    should_continue_macro = _should_continue_tool_call
    should_continue_smart_money = _should_continue_tool_call
    should_continue_volume_price = _should_continue_tool_call

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""
        debate_state = state.get("investment_debate_state") or {}
        if (
            safe_int(debate_state.get("count", 0), 0) >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if str(debate_state.get("current_speaker") or "").startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        risk_state = state.get("risk_debate_state") or {}
        if (
            safe_int(risk_state.get("count", 0), 0) >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Risk Judge"
        latest_speaker = str(risk_state.get("latest_speaker") or "")
        if latest_speaker.startswith("Aggressive"):
            return "Conservative Analyst"
        if latest_speaker.startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"

    def should_revise_after_risk_judge(self, state: AgentState) -> str:
        """Determine whether the trader must revise the plan after the risk judge."""
        feedback = state.get("risk_feedback_state", {})
        if (
            feedback.get("revision_required")
            and safe_int(feedback.get("retry_count", 0), 0) <= safe_int(feedback.get("max_retries", 1), 1)
        ):
            return "Trader"
        return "END"
