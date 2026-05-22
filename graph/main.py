from langgraph.graph import StateGraph, START, END
from graph.state import AgentState
from graph.nodes import (
    crisis_intervention,
    denver_model
)

def build_graph():
    workflow = StateGraph(AgentState)

    # 註冊節點
    workflow.add_node("crisis_intervention", crisis_intervention)
    workflow.add_node("denver_model", denver_model)

    # 🚀 直接從 denver_model 開始，合併判斷與生成
    workflow.add_edge(START, "denver_model")

    # 根據 denver_model 產出的 criticality_level 來決定是否轉向危機處理
    def router(state: AgentState):
        if state.get("criticality_level") == "High":
            return "crisis"
        return "end"

    workflow.add_conditional_edges(
        "denver_model",
        router,
        {
            "crisis": "crisis_intervention",
            "end": END,
        }
    )

    workflow.add_edge("crisis_intervention", END)

    return workflow.compile()
