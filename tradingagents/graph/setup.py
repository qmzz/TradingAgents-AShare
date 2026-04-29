# TradingAgents/graph/setup.py

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils.agent_states import AgentState

from .conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)

# Per-agent timeout (seconds). If an agent exceeds this, it is skipped and a
# fallback empty report is produced so the rest of the pipeline can continue.
_AGENT_TIMEOUT = int(__import__("os").environ.get("TA_AGENT_TIMEOUT", "600"))

# Per-agent retry settings
_AGENT_MAX_RETRIES = int(__import__("os").environ.get("TA_AGENT_RETRIES", "2"))
_AGENT_RETRY_BASE_DELAY = float(__import__("os").environ.get("TA_AGENT_RETRY_DELAY", "3.0"))

# Map of analyst node names → the state key they write into
_ANALYST_REPORT_KEYS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "macro": "macro_report",
    "smart_money": "smart_money_report",
    "volume_price": "volume_price_report",
}


def _is_retriable_error(exc: Exception) -> bool:
    """Determine if an exception is worth retrying (transient/infra errors only)."""
    exc_type = type(exc).__name__
    exc_module = type(exc).__module__

    # Timeout / connection errors → retry
    if exc_type in (
        "TimeoutError", "asyncio.TimeoutError", "ReadTimeout",
        "ConnectTimeout", "APIConnectionError", "APITimeoutError",
        "RateLimitError", "InternalServerError", "ServiceUnavailableError",
    ):
        return True

    # OpenAI SDK errors (all connection/network related)
    if "openai" in exc_module:
        if exc_type in ("APIConnectionError", "APITimeoutError", "RateLimitError",
                        "InternalServerError", "APIStatusError"):
            return True

    # HTTP 5xx → retry
    if hasattr(exc, "status_code") and exc.status_code and exc.status_code >= 500:
        return True

    return False


def _make_safe_node(original_node, agent_name: str, report_key: str = ""):
    """Wrap an async graph node with timeout + retry + error isolation.

    Retry logic:
    - Only retries on transient errors (network, timeout, 5xx, rate limit)
    - Never retries on business logic errors or parsing failures
    - Exponential backoff: base_delay × 2^attempt
    - Max 2 retries by default (configurable via TA_AGENT_RETRIES)

    Timeout:
    - Per-agent timeout (default 600s, configurable via TA_AGENT_TIMEOUT)
    - On timeout → treated as non-retriable, returns fallback report

    Error isolation:
    - Any failure after all retries → returns fallback report instead of crashing pipeline
    """
    async def safe_node(state: AgentState) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(_AGENT_MAX_RETRIES + 1):
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    original_node(state), timeout=_AGENT_TIMEOUT
                )
                elapsed = time.monotonic() - t0
                if attempt > 0:
                    logger.info(
                        f"[Agent] {agent_name} succeeded after {attempt} retry(s) "
                        f"in {elapsed:.1f}s"
                    )
                else:
                    logger.info(f"[Agent] {agent_name} completed in {elapsed:.1f}s")
                return result

            except asyncio.TimeoutError as exc:
                elapsed = time.monotonic() - t0
                logger.error(f"[Agent] {agent_name} timeout after {elapsed:.1f}s — not retried")
                # Timeout is NOT retried (the agent is stuck, retrying won't help)
                fallback = (
                    f"⚠️ {agent_name} 分析超时（{_AGENT_TIMEOUT}秒），已跳过。\n\n"
                    f"后续分析将基于其他可用报告进行。"
                )
                return {report_key: fallback} if report_key else {}

            except Exception as exc:
                elapsed = time.monotonic() - t0
                last_exc = exc

                if not _is_retriable_error(exc) or attempt == _AGENT_MAX_RETRIES:
                    # Non-retriable or exhausted retries
                    error_type = "异常" if not _is_retriable_error(exc) else "重试耗尽"
                    logger.error(
                        f"[Agent] {agent_name} {error_type} after {elapsed:.1f}s "
                        f"({attempt+1} attempt(s)): {exc}"
                    )
                    fallback = (
                        f"⚠️ {agent_name} 分析失败（{error_type}，耗时 {elapsed:.1f}s）：{exc}\n\n"
                        f"由于上游异常，本报告为空。后续分析将基于其他可用报告进行。"
                    )
                    return {report_key: fallback} if report_key else {}

                # Retriable error — wait and retry
                delay = _AGENT_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"[Agent] {agent_name} attempt {attempt+1} failed: {exc}. "
                    f"Retrying in {delay:.0f}s…"
                )
                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        fallback = f"⚠️ {agent_name} 分析失败：{last_exc}"
        return {report_key: fallback} if report_key else {}

    safe_node.__name__ = f"safe_{agent_name}"
    safe_node.__qualname__ = safe_node.__name__
    return safe_node


def _load_agent_factories() -> dict[str, Any]:
    """Load graph node factories lazily to avoid circular imports.

    Analyst modules import ``tradingagents.graph.intent_parser``; if this module
    eagerly imports ``tradingagents.agents`` during package initialization, the
    partially initialized package can miss symbols such as
    ``create_market_analyst``. Delaying these imports until graph construction
    keeps module import order stable for API requests and scheduled jobs.
    """
    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.macro_analyst import create_macro_analyst
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.agents.analysts.smart_money_analyst import create_smart_money_analyst
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from tradingagents.agents.analysts.volume_price_analyst import create_volume_price_analyst
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from tradingagents.agents.trader.trader import create_trader

    return {
        "create_aggressive_debator": create_aggressive_debator,
        "create_bear_researcher": create_bear_researcher,
        "create_bull_researcher": create_bull_researcher,
        "create_conservative_debator": create_conservative_debator,
        "create_fundamentals_analyst": create_fundamentals_analyst,
        "create_macro_analyst": create_macro_analyst,
        "create_market_analyst": create_market_analyst,
        "create_neutral_debator": create_neutral_debator,
        "create_news_analyst": create_news_analyst,
        "create_research_manager": create_research_manager,
        "create_risk_manager": create_risk_manager,
        "create_smart_money_analyst": create_smart_money_analyst,
        "create_social_media_analyst": create_social_media_analyst,
        "create_volume_price_analyst": create_volume_price_analyst,
        "create_trader": create_trader,
    }


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,
        deep_thinking_llm: ChatOpenAI,
        tool_nodes: Dict[str, ToolNode],
        bull_memory,
        bear_memory,
        trader_memory,
        invest_judge_memory,
        risk_manager_memory,
        conditional_logic: ConditionalLogic,
        data_collector=None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.data_collector = data_collector

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals", "macro", "smart_money"],
        checkpointer=None
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include.
            checkpointer: Optional LangGraph checkpointer for state persistence.
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        factories = _load_agent_factories()

        # Create analyst nodes
        analyst_nodes = {}
        tool_nodes = {}
        done_nodes = {}

        def analyst_done_node(_state):
            return {}

        if "market" in selected_analysts:
            analyst_nodes["market"] = _make_safe_node(
                factories["create_market_analyst"](self.quick_thinking_llm, self.data_collector),
                "Market Analyst", "market_report"
            )
            tool_nodes["market"] = self.tool_nodes["market"]
            done_nodes["market"] = analyst_done_node

        if "social" in selected_analysts:
            analyst_nodes["social"] = _make_safe_node(
                factories["create_social_media_analyst"](self.quick_thinking_llm, self.data_collector),
                "Social Sentiment Analyst", "sentiment_report"
            )
            tool_nodes["social"] = self.tool_nodes["social"]
            done_nodes["social"] = analyst_done_node

        if "news" in selected_analysts:
            analyst_nodes["news"] = _make_safe_node(
                factories["create_news_analyst"](self.quick_thinking_llm, self.data_collector),
                "News Analyst", "news_report"
            )
            tool_nodes["news"] = self.tool_nodes["news"]
            done_nodes["news"] = analyst_done_node

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = _make_safe_node(
                factories["create_fundamentals_analyst"](self.quick_thinking_llm, self.data_collector),
                "Fundamentals Analyst", "fundamentals_report"
            )
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]
            done_nodes["fundamentals"] = analyst_done_node

        if "macro" in selected_analysts:
            analyst_nodes["macro"] = _make_safe_node(
                factories["create_macro_analyst"](self.quick_thinking_llm, self.data_collector),
                "Macro Analyst", "macro_report"
            )
            tool_nodes["macro"] = self.tool_nodes["macro"]
            done_nodes["macro"] = analyst_done_node

        if "smart_money" in selected_analysts:
            analyst_nodes["smart_money"] = _make_safe_node(
                factories["create_smart_money_analyst"](self.quick_thinking_llm, self.data_collector),
                "Smart Money Analyst", "smart_money_report"
            )
            tool_nodes["smart_money"] = self.tool_nodes["smart_money"]
            done_nodes["smart_money"] = analyst_done_node

        if "volume_price" in selected_analysts:
            analyst_nodes["volume_price"] = _make_safe_node(
                factories["create_volume_price_analyst"](self.quick_thinking_llm, self.data_collector),
                "Volume Price Analyst", "volume_price_report"
            )
            tool_nodes["volume_price"] = self.tool_nodes["volume_price"]
            done_nodes["volume_price"] = analyst_done_node

        # Create researcher and manager nodes (wrapped for error isolation)
        bull_researcher_node = _make_safe_node(
            factories["create_bull_researcher"](self.quick_thinking_llm, self.bull_memory),
            "Bull Researcher",
        )
        bear_researcher_node = _make_safe_node(
            factories["create_bear_researcher"](self.quick_thinking_llm, self.bear_memory),
            "Bear Researcher",
        )
        research_manager_node = _make_safe_node(
            factories["create_research_manager"](self.deep_thinking_llm, self.invest_judge_memory),
            "Research Manager",
        )
        trader_node = _make_safe_node(
            factories["create_trader"](self.quick_thinking_llm, self.trader_memory),
            "Trader",
        )

        # Create risk analysis nodes (wrapped for error isolation)
        aggressive_analyst = _make_safe_node(
            factories["create_aggressive_debator"](self.quick_thinking_llm),
            "Aggressive Analyst",
        )
        neutral_analyst = _make_safe_node(
            factories["create_neutral_debator"](self.quick_thinking_llm),
            "Neutral Analyst",
        )
        conservative_analyst = _make_safe_node(
            factories["create_conservative_debator"](self.quick_thinking_llm),
            "Conservative Analyst",
        )
        risk_manager_node = _make_safe_node(
            factories["create_risk_manager"](self.deep_thinking_llm, self.risk_manager_memory),
            "Portfolio Manager (Risk Judge)",
        )

        # Create workflow
        workflow = StateGraph(AgentState)

        def analyst_display_name(analyst_type: str) -> str:
            """Convert analyst_type key to display name, e.g. 'smart_money' -> 'Smart Money'."""
            return analyst_type.replace("_", " ").title()

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_display_name(analyst_type)} Analyst", node)
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])
            workflow.add_node(f"{analyst_display_name(analyst_type)} Analyst Done", done_nodes[analyst_type])

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Risk Judge", risk_manager_node)

        # Define edges
        # Fan out all selected analysts in parallel from START
        for analyst_type in selected_analysts:
            workflow.add_edge(START, f"{analyst_display_name(analyst_type)} Analyst")

        # Each analyst runs independently, then fans in to Bull Researcher
        for analyst_type in selected_analysts:
            current_analyst = f"{analyst_display_name(analyst_type)} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_done = f"{analyst_display_name(analyst_type)} Analyst Done"
            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                {
                    "continue": current_tools,
                    "done": current_done,
                },
            )
            workflow.add_edge(current_tools, current_analyst)

        # All analysts complete → Bull Researcher (start debate)
        workflow.add_edge(
            [f"{analyst_display_name(analyst_type)} Analyst Done" for analyst_type in selected_analysts],
            "Bull Researcher",
        )

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Risk Judge": "Risk Judge",
            },
        )

        workflow.add_conditional_edges(
            "Risk Judge",
            self.conditional_logic.should_revise_after_risk_judge,
            {
                "Trader": "Trader",
                "END": END,
            },
        )

        # Compile and return
        return workflow.compile(checkpointer=checkpointer)
