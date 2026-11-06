"""Graph wiring.

START -> classify -+-> benefits --+-> plan -> human_review -+-> execute -> confirm -> END
                   +-> leave -----+   (interrupt)          +-> END (reject)
                   +-> escalate -> END
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import AgentState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)

    g.add_node("classify", nodes.classify)
    g.add_node("escalate", nodes.escalate)
    g.add_node("benefits", nodes.benefits_specialist)
    g.add_node("leave", nodes.leave_specialist)
    g.add_node("plan", nodes.plan)
    g.add_node("human_review", nodes.human_review)
    g.add_node("execute", nodes.execute)
    g.add_node("confirm", nodes.confirm)

    g.add_edge(START, "classify")
    # Returning a list from the router fans out in parallel; `plan` runs
    # once after every branch in that superstep finishes.
    g.add_conditional_edges(
        "classify", nodes.route_after_classify, ["benefits", "leave", "escalate"]
    )
    g.add_edge("escalate", END)
    g.add_edge("benefits", "plan")
    g.add_edge("leave", "plan")
    g.add_edge("plan", "human_review")
    g.add_conditional_edges(
        "human_review", nodes.route_after_review, {"execute": "execute", "end": END}
    )
    g.add_edge("execute", "confirm")
    g.add_edge("confirm", END)

    # A checkpointer is required for interrupt()/resume and time travel.
    return g.compile(checkpointer=checkpointer)
