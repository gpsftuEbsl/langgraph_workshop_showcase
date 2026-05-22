import json
import re
import os
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from graph.state import AgentState

# ==========================================
# 模擬資料庫 (Mock Data)
# 註：原始專案的完整題庫、早療指引數據與場景庫 (data/ 目錄) 屬專利與智慧財產權，已從此公開 Showcase 中排除。
# ==========================================

AVAILABLE_SCENES = {
    "running": {"filename": "running.png", "description": "孩童奔跑的場景"},
    "crying": {"filename": "crying.png", "description": "孩童哭鬧/挫折的場景"},
    "sharing": {"filename": "sharing.png", "description": "孩童分享玩具/合作的場景"},
    "hugging": {"filename": "hugging.png", "description": "家長擁抱孩子/成功的場景"}
}

INITIAL_SCENES = {
    "running": {"filename": "running.png", "description": "開始挑戰"},
    "crying": {"filename": "crying.png", "description": "面臨挫折"}
}

OUTCOME_SCENES = {
    "sharing": {"filename": "sharing.png", "description": "正向社交結果"},
    "hugging": {"filename": "hugging.png", "description": "正向情緒依附"}
}

def get_llm(temperature=0.2):
    """初始化並回傳 Gemini LLM 實例。"""
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        return None
    return ChatGoogleGenerativeAI(
        model='gemini-3.1-flash-lite-preview',
        google_api_key=api_key,
        temperature=temperature,
        timeout=30,
        max_retries=2
    )

def _extract_text(content) -> str:
    """將 Gemini response.content 統一轉為純字串。"""
    if isinstance(content, list):
        return "".join([
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ])
    return str(content)

def _extract_json(text: str):
    """從 LLM 輸出中穩健地提取 JSON"""
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)

def crisis_intervention(state: AgentState) -> dict:
    """
    [危機處理節點 - Showcase 抽象化版本]
    當判定狀況危急度為 High 時，安全第一，不進行遊戲，直接引導家長尋求緊急協助資源。
    """
    llm = get_llm(temperature=0.0)
    
    # 抽象化的系統 Prompt，隱藏核心業務邏輯
    system_prompt = """你是一個危機處理專家。
根據輸入的情境，提供最適合的求救專線與緊急處置建議。
語氣必須沉穩、堅定且具同理心。
【重要】請回傳純 JSON 格式，包含以下欄位：
- "parent_advice": 50 字以內的家長處置建議與求助專線。
- "scene": "running"
"""
    user_prompt = f"家長回報狀況：{state.get('user_input', '')}\n孩童行為：{state.get('child_behavior', '')}"
    
    if not llm:
        # 沙盒模式/無 API Key 時的 Mock 機制
        return {
            "parent_advice": "【系統提示】請立即確保您與孩子的安全。建議撥打 113 保護專線或 110 求助。",
            "child_story": "請先與爸爸媽媽待在安全的地方喔。",
            "scene": "running.png"
        }
        
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        content = _extract_text(response.content)
        data = _extract_json(content)
        
        scene_key = data.get("scene", "running")
        scene_filename = AVAILABLE_SCENES.get(scene_key, AVAILABLE_SCENES["running"])["filename"]
        
        return {
            "parent_advice": data.get("parent_advice", "請立刻確保安全並撥打 113。"),
            "child_story": "請先確保安全。",
            "scene": scene_filename
        }
    except Exception as e:
        return {
            "parent_advice": "請立即確保您與孩子的安全，若遇緊急狀況請撥打 113 或 110。",
            "child_story": "請先確保安全。",
            "scene": "running.png"
        }

def denver_model(state: AgentState) -> dict:
    """
    [丹佛模式故事生成節點 - Showcase 抽象化版本]
    負責分析家長描述的行為特徵，判斷危急度，並為孩子設計具備正負回饋機制的互動式遊戲情境。
    """
    llm = get_llm(temperature=0.7)
    child_behavior = state.get('child_behavior', '')
    is_in_game = (child_behavior == "遊戲互動")
    
    if not llm:
        # 沙盒模式/無 API Key 時的 Mock 機制
        if is_in_game:
            return {
                "child_story": "【互動遊戲中】小熊拍拍胸口，對你笑了笑。你決定要跟小熊分享你的玩具，還是自己跑掉呢？",
                "b1": "分享玩具",
                "b2": "自己跑掉",
                "scene": "sharing.png"
            }
        else:
            return {
                "child_story": "【遊戲已開啟】冒險森林裡有一隻迷路的小熊，看起來有點緊張，我們去看看他吧！",
                "b1": "走過去打招呼",
                "b2": "大聲叫媽媽",
                "scene": "running.png",
                "parent_advice": "【早期介入引導】孩子目前有社交互動方面的引導空間。建議在日常中多進行「玩具輪流玩」或「情緒繪本共讀」，建立輪流與互動概念。",
                "child_behavior": "社交互動引導",
                "criticality_level": "Low"
            }

    # 抽象化的系統 Prompt，隱藏核心業務邏輯
    if is_in_game:
        system_prompt = """你是一個兒童故事家。請根據小朋友的選擇延續故事。
請回傳純 JSON 格式，包含以下欄位：
- "child_story": 50字以內直接對孩子說的有趣故事。
- "b1": 正向按鈕文字 (5字以內)。
- "b2": 負向按鈕文字 (5字以內)。
- "scene": 適合的場景代號 ("sharing" 或 "hugging")。
"""
    else:
        system_prompt = """你是一個早期介入引導專家。請根據家長描述，評估危急度與行為特徵，並設計開啟的故事。
請回傳純 JSON 格式，包含以下欄位：
- "criticality_level": 危急度 ("Low", "Medium", "High")。
- "child_behavior": 擷取出的行為特徵。
- "parent_advice": 150字以內給家長的丹佛模式專業建議。
- "child_story": 50字以內對孩子說的故事。
- "b1": 正向按鈕 (5字以內)。
- "b2": 負向按鈕 (5字以內)。
- "scene": 適合場景代號 ("running" 或 "crying")。
"""
    user_prompt = f"家長描述：{state.get('user_input', '')} | 行為：{child_behavior} | 危急度：{state.get('criticality_level', '')}"
    
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        content = _extract_text(response.content)
        data = _extract_json(content)
        
        scene_key = data.get("scene", "running")
        scene_source = OUTCOME_SCENES if is_in_game else INITIAL_SCENES
        scene_filename = scene_source.get(scene_key, AVAILABLE_SCENES.get(scene_key, AVAILABLE_SCENES["running"]))["filename"]
        
        res = {
            "child_story": data.get("child_story", "我們稍微休息一下喔！"),
            "b1": data.get("b1", "繼續探索"),
            "b2": data.get("b2", "休息一下"),
            "scene": scene_filename
        }
        
        if not is_in_game:
            res.update({
                "parent_advice": data.get("parent_advice", "建議與孩子一同探索社交活動。"),
                "child_behavior": data.get("child_behavior", "行為評估中"),
                "criticality_level": data.get("criticality_level", "Low")
            })
            
        return res
    except Exception as e:
        return {
            "child_story": "我們稍微休息一下喔！",
            "b1": "繼續探索",
            "b2": "回首頁",
            "scene": "running.png"
        }
