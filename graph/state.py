from typing import TypedDict

class AgentState(TypedDict, total=False):
    user_input: str          # 使用者原始輸入文字
    child_behavior: str      # 孩童行為
    criticality_level: str   # 情況危急度: "Low", "Medium", "High"
    parent_advice: str       # 給家長的專業建議與安撫
    child_story: str         # 給孩子的互動遊戲故事
    b1: str                  # 選項按鈕 1
    b2: str                  # 選項按鈕 2
    scene: str               # 根據情境挑選的圖片檔名
    error: str               # 錯誤訊息（若有）
