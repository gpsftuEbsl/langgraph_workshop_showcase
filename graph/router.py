from graph.state import AgentState

def criticality_router(state: AgentState) -> str:
    level = state.get("criticality_level", "Medium")
    if level == "High":
        return "crisis"
    else:
        return "denver"
