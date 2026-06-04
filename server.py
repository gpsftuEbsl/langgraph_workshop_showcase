import json
import urllib.parse
import os
import uuid
import asyncio
import hmac
import hashlib
import base64
import urllib.request
import urllib.error
import re
from fastapi import FastAPI, Query, BackgroundTasks, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, Literal
from dotenv import load_dotenv

load_dotenv()

from graph.main import build_graph 
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# --- 全域變數 ---
game_storage = {}  # 存放遊戲初始劇本 {id: data}
choice_cache = {}  # 存放預生成的下一步結果 {f"{userId}:{choice}": result}
user_tasks = {}    # 存放每個使用者正在跑的背景任務 {userId: [asyncio.Task]}

# 存放 LINE 使用者的遊戲狀態 {user_id: state_dict}
user_game_states = {}

app = FastAPI(title="Early Intervention & Turtle Soup Agent API")

# --- 跨域設定 (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False, 
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 錯誤處理：詳細列印 422 驗證錯誤訊息 ---
from fastapi.exceptions import RequestValidationError
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    print(f"❌ [422 Error] {request.method} {request.url}")
    print(f"Details: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# 強制攔截 OPTIONS (Preflight) 確保網頁連線不受阻擋
@app.options("/api/{rest_of_path:path}")
async def preflight_handler():
    return JSONResponse(status_code=200, content={"message": "OK"})

# --- 初始化大腦 ---
graph = build_graph()

app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

@app.get("/")
async def read_index():
    return RedirectResponse(url="/frontend/index.html")

# --- 定義 Request 格式 ---
class RecommendRequest(BaseModel):
    user_input: str

class AnalyzeRequest(BaseModel):
    message: str
    user_id: str

class ChoiceRequest(BaseModel):
    choice: str
    userId: Optional[str] = None
    b1: Optional[str] = ""
    b2: Optional[str] = ""
    isCheck: bool = False
    isResult: bool = False


# ==========================================
# 🐢 海龜湯 (Turtle Soup) 核心資料與邏輯 🐢
# ==========================================

DEFAULT_STORIES = {
    "1": {
        "title": "始祖海龜湯",
        "surface": "一個男子在一家能看見海的餐廳點了一碗海龜湯。他只吃了幾口，就十分驚訝地詢問店員：這真的是海龜湯嗎？店員肯定地回答：是的，這是貨真價實的海龜湯。男子聽完後沉思了一會兒，隨後便走到餐廳外的懸崖，跳海自殺了。請問究竟發生了什麼事？",
        "truth": "該名男子在多年前曾遭遇一場嚴重的海難，與幾名同伴受困在救生艇上，在茫茫大海上漂流了許久。在彈盡糧絕、大家快要餓死之際，男子的同伴遞給他一碗肉湯，並安慰他這是好不容易抓到的海龜肉煮成的湯，男子靠著這碗湯熬過難關，最終幸運獲救。多年後，男子來到這家海濱餐廳，滿懷懷念地點了真正的海龜湯，但一入口卻發現口感與當年吃到的完全不同。此時他才驚覺，當年同伴為了讓他活下去，給他吃的根本不是海龜肉，而是已經不幸去世的同伴的肉。無法承受巨大罪惡感與心理衝擊的他，最終選擇跳下懸崖結束生命。"
    },
    "2": {
        "title": "絕望的 DJ",
        "surface": "一個男人正在深夜的高速公路上開車。他打開了車上的廣播，聆聽稍早的節目回放。聽了幾分鐘後，他突然把車停在路邊，在車內絕望地大哭，隨後拿出一把槍結束了自己的生命。請推理事情的經過。",
        "truth": "男人是一個廣播電台的知名 DJ。兩個小時前，他在家裡謀殺了自己的妻子。為了製造「自己當時正在電台上班」的不在場證明，他事先在播音室放了一捲非常長的預錄節目帶。沒想到他開車逃逸時打開廣播回放，卻聽見廣播裡傳來錄音帶跳針卡帶的怪聲（或是完全無聲）。他意識到自己的不在場證明徹底毀了，警方馬上就會查到他頭上，絕望之下只好畏罪自殺。"
    }
}

class TurtleSoupResponse(BaseModel):
    answer: Literal["是", "不是", "是也不是", "方向正確，請更詳細提問", "不重要"] = Field(
        description="對玩家提問的標準回答，必須是指定選項之一"
    )
    reason: str = Field(description="推導此回答的邏輯原因（對玩家隱藏，僅用於模型自我思考）")

async def ask_gemini_turtle_soup(question: str, surface: str, truth: str) -> dict:
    """呼叫 Gemini 進行海龜湯的精準回答判斷，優先使用結構化輸出以維持穩定性。"""
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    llm = None
    if api_key:
        try:
            llm = ChatGoogleGenerativeAI(
                model='gemini-2.5-flash',
                google_api_key=api_key,
                temperature=0.1,
                timeout=30,
                max_retries=2
            )
        except Exception as e:
            print(f"Error initializing ChatGoogleGenerativeAI: {e}")
            
    if not llm:
        # 沙盒/無 API 金鑰時的 Mock 規則，便於測試
        q_lower = question.lower()
        if "海龜" in q_lower or "肉" in q_lower or "吃" in q_lower:
            return {"answer": "是", "reason": "沙盒模式對關鍵字 '海龜/肉/吃' 回應 是"}
        elif "自殺" in q_lower or "死" in q_lower:
            return {"answer": "方向正確，請更詳細提問", "reason": "沙盒模式對關鍵字 '自殺/死' 回應 方向正確"}
        else:
            return {"answer": "不重要", "reason": "沙盒模式預設回應"}

    system_instruction = f"""你是一個海龜湯（情境猜謎）的遊戲主持人（關主）。
湯面（玩家看到的故事）：{surface}
湯底（背後的真相）：{truth}

玩家會針對這個故事問你問題，請你根據「湯底」的真相來判斷。
你的回答必須且只能從以下五個選項中選擇一個：
1. 是
2. 不是
3. 是也不是
4. 方向正確，請更詳細提問
5. 不重要

請站在客觀且嚴格的立場判定，如果問題與真相的關鍵線索完全無關，請回答「不重要」。"""

    try:
        structured_llm = llm.with_structured_output(TurtleSoupResponse)
        response = await structured_llm.ainvoke([
            SystemMessage(content=system_instruction),
            HumanMessage(content=question)
        ])
        return {
            "answer": response.answer,
            "reason": response.reason
        }
    except Exception as e:
        print(f"Gemini Structured Output Error: {e}, attempting text-based fallback...")
        try:
            # 備用的 JSON 文字生成
            fallback_instruction = system_instruction + "\n\n【重要】請務必回傳 JSON 格式，例如：\n```json\n{\"answer\": \"是\", \"reason\": \"原因\"}\n```"
            response = await llm.ainvoke([
                SystemMessage(content=fallback_instruction),
                HumanMessage(content=question)
            ])
            text = response.content.strip()
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
            if match:
                text = match.group(1).strip()
            data = json.loads(text)
            return {
                "answer": data.get("answer", "是也不是"),
                "reason": data.get("reason", "Fallback parse successful")
            }
        except Exception as e2:
            print(f"Fallback parse failed: {e2}")
            return {
                "answer": "是也不是",
                "reason": f"API 呼叫或 JSON 解析失敗: {str(e2)}"
            }

async def get_gemini_hint(surface: str, truth: str) -> str:
    """利用 Gemini 產生適度引導但不劇透的簡短提示。"""
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    llm = None
    if api_key:
        try:
            llm = ChatGoogleGenerativeAI(
                model='gemini-2.5-flash',
                google_api_key=api_key,
                temperature=0.7,
                timeout=30,
                max_retries=2
            )
        except Exception as e:
            print(f"Error initializing ChatGoogleGenerativeAI for hint: {e}")
            
    if not llm:
        return "試著從主角在海難中吃到了什麼，或是真的海龜湯有什麼不同來思考。"

    system_instruction = f"""你是一個海龜湯（情境猜謎）的遊戲主持人（關主）。
湯面：{surface}
湯底：{truth}

玩家目前卡住了，請根據湯底真相，提供一個非常簡短、具啟發性但「絕對不能直接透露答案（劇透）」的提示。
提示字數限制在 20 字以內，語氣帶有神秘感。"""

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_instruction),
            HumanMessage(content="請給我一個提示。")
        ])
        return response.content.strip()
    except Exception as e:
        print(f"Hint generation error: {e}")
        return "試著從主角的動機與當時的環境開始聯想。"

async def handle_turtle_soup_flow(user_id: str, user_msg: str) -> str:
    """處理 LINE 使用者的海龜湯遊戲狀態與對話流。"""
    state = user_game_states.get(user_id)
    
    # 快捷指令
    if user_msg in ["開始", "選單", "重設", "reset", "help", "開局"]:
        user_game_states[user_id] = {"status": "choosing"}
        return (
            "🐢 歡迎來到 AI 海龜湯關主！ 🐢\n"
            "請選擇想挑戰的劇本，或輸入「自訂」來上傳你的海龜湯：\n\n"
            "1️⃣ 【始祖海龜湯】（經典吃肉事件）\n"
            "2️⃣ 【絕望的 DJ】（廣播的致命失誤）\n\n"
            "👉 請輸入數字 1 或 2 開始，或輸入「自訂」開始你的創作。"
        )
        
    if not state:
        user_game_states[user_id] = {"status": "choosing"}
        return (
            "🐢 歡迎來到 AI 海龜湯關主！ 🐢\n"
            "請輸入「開始」或「選單」來開始遊戲！"
        )
        
    status = state.get("status")
    
    # 選擇劇本階段
    if status == "choosing":
        if user_msg in DEFAULT_STORIES:
            story = DEFAULT_STORIES[user_msg]
            user_game_states[user_id] = {
                "status": "playing",
                "title": story["title"],
                "surface": story["surface"],
                "truth": story["truth"]
            }
            return (
                f"🎬【遊戲開始】《{story['title']}》\n\n"
                f"🥣 湯面：{story['surface']}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💬 請開始向我提問！我只會回答：\n"
                f"「是」、「不是」、「是也不是」、「方向正確，請更詳細提問」或「不重要」。\n"
                f"💡 輸入「提示」獲取線索，輸入「答案」或「真相」可直接揭曉真相。"
            )
        elif user_msg == "自訂":
            user_game_states[user_id] = {"status": "custom_surface"}
            return "✍️ 請輸入自訂的【湯面】（故事描述）："
        else:
            return "⚠️ 請輸入數字 1 或 2 開始，或輸入「自訂」。"
            
    # 自訂湯面階段
    elif status == "custom_surface":
        user_game_states[user_id] = {
            "status": "custom_truth",
            "temp_surface": user_msg
        }
        return (
            f"✅ 已收到湯面！\n\n"
            f"✍️ 請輸入對應的【湯底】（真相）："
        )
        
    # 自訂湯底階段
    elif status == "custom_truth":
        surface = state.get("temp_surface")
        user_game_states[user_id] = {
            "status": "playing",
            "title": "自訂海龜湯",
            "surface": surface,
            "truth": user_msg
        }
        return (
            f"🎬【自訂遊戲開始】\n\n"
            f"🥣 湯面：{surface}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💬 請開始向我提問！我只會回答：\n"
            f"「是」、「不是」、「是也不是」、「方向正確，請更詳細提問」或「不重要」。\n"
            f"💡 輸入「答案」或「真相」可直接揭曉真相。"
        )
        
    # 遊戲進行中階段
    elif status == "playing":
        if user_msg in ["答案", "真相", "揭曉", "揭曉真相", "放棄"]:
            title = state.get("title")
            truth = state.get("truth")
            user_game_states[user_id] = {"status": "choosing"} # 重設狀態回到選擇
            return (
                f"🔓【揭曉真相】《{title}》\n\n"
                f"💡 湯底：\n{truth}\n\n"
                f"🎉 感謝遊玩！輸入「開始」可以重新選擇遊戲。"
            )
        elif user_msg in ["提示", "hint", "線索"]:
            surface = state.get("surface")
            truth = state.get("truth")
            hint = await get_gemini_hint(surface, truth)
            return f"💡 提示：{hint}"
        else:
            # 進行問題判定
            surface = state.get("surface")
            truth = state.get("truth")
            res = await ask_gemini_turtle_soup(user_msg, surface, truth)
            answer = res.get("answer", "不重要")
            print(f"[Game Logic] Q: {user_msg} | A: {answer} (Reason: {res.get('reason')})")
            return f"🤖：{answer}"
            
    return "系統發生異常，請輸入「開始」重置遊戲。"


# ==========================================
# 💬 LINE 訊息傳送與驗證機制 💬
# ==========================================

def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """驗證 LINE Webhook 的簽章，確保請求來自 LINE 官方伺服器。"""
    if not channel_secret:
        return True
    hash = hmac.new(channel_secret.encode('utf-8'), body, hashlib.sha256).digest()
    calc_signature = base64.b64encode(hash).decode('utf-8')
    return hmac.compare_digest(calc_signature, signature)

def reply_to_line(reply_token: str, messages: list, channel_access_token: str):
    """利用 HTTP POST 發送回覆給 LINE 官方伺服器。使用內建 urllib 以保持極致穩定性。"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel_access_token}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": messages
    }
    req_data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        print(f"❌ [LINE API Error] Status: {e.code} | Message: {err_msg}")
        raise e


# --- LINE Webhook Callback Endpoint ---
@app.post("/callback")
async def line_callback(
    request: Request,
    x_line_signature: Optional[str] = Header(None)
):
    body = await request.body()
    body_str = body.decode('utf-8')
    
    # 讀取 LINE 設定
    channel_secret = os.getenv("LINE_CHANNEL_SECRET", "")
    channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    
    if not channel_access_token:
        print("⚠️ Warning: LINE_CHANNEL_ACCESS_TOKEN is not set.")
        return JSONResponse(status_code=200, content={"message": "Token not configured"})
        
    # 驗證簽章
    if channel_secret and x_line_signature:
        if not verify_signature(body, x_line_signature, channel_secret):
            print("❌ Invalid signature")
            raise HTTPException(status_code=400, detail="Invalid signature")
            
    try:
        payload = json.loads(body_str)
        events = payload.get("events", [])
        for event in events:
            if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
                reply_token = event.get("replyToken")
                user_id = event.get("source", {}).get("userId")
                user_msg = event.get("message", {}).get("text", "").strip()
                
                # 處理訊息並取得回覆內容
                reply_text = await handle_turtle_soup_flow(user_id, user_msg)
                
                # 發送回覆給 LINE
                reply_to_line(reply_token, [{"type": "text", "text": reply_text}], channel_access_token)
    except Exception as e:
        print(f"Error handling LINE event: {e}")
        
    return JSONResponse(status_code=200, content={"message": "OK"})


# ==========================================
# 🎮 早期介入引導系統 (Showcase 原有 APIs) 🎮
# ==========================================

async def _fetch_one_choice(user_id: str, text: str):
    """背景任務：計算單個按鈕，並存入快取。"""
    from graph.nodes import denver_model
    clean_text = text.strip()
    cache_key = f"{user_id}:{clean_text}"
    if cache_key in choice_cache: return
    try:
        prompt = f"小朋友選擇了：{clean_text}。請給予下一個情境的簡短發展。"
        result = await asyncio.to_thread(denver_model, {"user_input": prompt, "child_behavior": "遊戲互動"})
        choice_cache[cache_key] = {
            "text": result.get("child_story", "處理中..."),
            "b1": result.get("b1", "繼續探索"),
            "b2": result.get("b2", "休息一下"),
            "scene": result.get("scene", "running")
        }
        print(f"[Cache] 預生成完成: {cache_key}")
    except asyncio.CancelledError:
        print(f"[Cache] 任務被取消: {cache_key}")
    except Exception as e:
        print(f"[Cache] 預處理失敗: {e}")

def start_pregen_task(user_id: str, b1: str, b2: str):
    """清理舊任務並為兩個選項啟動獨立的預生成任務。"""
    if user_id in user_tasks:
        for old_task in user_tasks[user_id]:
            if not old_task.done():
                old_task.cancel()
        user_tasks[user_id] = []

    # 🚀 啟動兩個獨立任務，互不等待
    t1 = asyncio.create_task(_fetch_one_choice(user_id, b1))
    t2 = asyncio.create_task(_fetch_one_choice(user_id, b2))
    
    if user_id not in user_tasks:
        user_tasks[user_id] = []
    user_tasks[user_id].extend([t1, t2])

@app.get("/api/check_cache")
async def check_cache(userId: str = Query(...), b1: str = Query(...), b2: str = Query(...)):
    """讓前端檢查按鈕是否已經預生成好。"""
    key1 = f"{userId}:{b1.strip()}"
    key2 = f"{userId}:{b2.strip()}"
    b1_ready = key1 in choice_cache
    b2_ready = key2 in choice_cache
    
    if not (b1_ready and b2_ready):
        print(f"[Check] {userId} | b1:{b1.strip()}({b1_ready}) | b2:{b2.strip()}({b2_ready})")
        
    return {
        "b1_ready": b1_ready,
        "b2_ready": b2_ready
    }

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    第一步：接收 LINE 訊息，呼叫 Gemini 產生劇情，存入 memory 並回傳短網址 ID
    """
    try:
        final_state = graph.invoke({"user_input": req.message})
        
        if final_state.get("error"):
            return {"reply_text": final_state.get("error")}

        behavior = final_state.get("child_behavior", "未知行為")
        parent_advice = final_state.get('parent_advice', '大腦思考中...')
        child_story = final_state.get('child_story', '系統處理中...')
        
        game_data = {
            "text": child_story,
            "b1": final_state.get("b1", "繼續探索"),
            "b2": final_state.get("b2", "休息一下"),
            "scene": final_state.get("scene", "running")
        }

        game_id = str(uuid.uuid4())[:8]
        game_storage[game_id] = game_data

        start_pregen_task(req.user_id, game_data["b1"], game_data["b2"])

        level = final_state.get("criticality_level", "Medium")
        level_map = {"Low": "🟢低", "Medium": "🟡未知", "High": "🔴高"}
        level_display = level_map.get(level, level)

        base_game_url = "https://gpsftuebsl.github.io/json-driven-ui"
        game_url = f"{base_game_url}/index.html?id={game_id}&uid={req.user_id}&openExternalBrowser=1"
        
        if level == "High":
            final_reply = (
                f"🚨狀況等級(僅供參考)：{level_display}\n"
                f"👶行為特徵：{behavior}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡緊急處置與求助資源：\n{parent_advice}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️請務必立刻確保您與孩子的安全，若遇緊急狀況請撥打 113 或 110 求助。\n"
                f"⚠️免責聲明：本系統產生之分析與建議僅供情境演練參考，無法取代專業醫療診斷。若遇緊急狀況或持續性問題，請務必尋求專業醫師或治療師協助。"
            )
        else:
            final_reply = (
                f"✅狀況等級(僅供參考)：{level_display}\n"
                f"👶行為特徵：{behavior}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡專家建議：\n{parent_advice}\n\n"
                f"🎮進入小幫手遊戲：\n{game_url}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️免責聲明：本系統產生之分析與建議僅供情境演練參考，無法取代專業醫療診斷。若遇緊急狀況或持續性問題，請務必尋求專業醫師或治療師協助。"
            )

        return {
            "reply_text": final_reply,
            "game_id": game_id
        }
    except Exception as e:
        print(f"Error in analyze: {e}")
        return {"reply_text": "分析過程發生錯誤，請稍後再試。"}

@app.get("/api/get_game")
async def get_game(id: str = Query(...), background_tasks: BackgroundTasks = None):
    data = game_storage.get(id)
    if not data:
        return JSONResponse(status_code=404, content={"message": "找不到該遊戲紀錄"})
    return data

@app.post("/api/choice")
async def handle_game_choice(req: ChoiceRequest, background_tasks: BackgroundTasks):
    try:
        user_id = req.userId or "anonymous"
        
        if req.isCheck:
            b1_ready = f"{user_id}:{req.b1.strip()}" in choice_cache
            b2_ready = f"{user_id}:{req.b2.strip()}" in choice_cache
            if not (b1_ready and b2_ready):
                print(f"[Check-via-POST] {user_id} | b1:{req.b1.strip()}({b1_ready}) | b2:{req.b2.strip()}({b2_ready})")
            return { "b1_ready": b1_ready, "b2_ready": b2_ready }

        clean_choice = req.choice.strip()
        cache_key = f"{user_id}:{clean_choice}"
        cached = choice_cache.pop(cache_key, None)

        if cached:
            print(f"[Cache] HIT: {cache_key}")
            result = cached
        else:
            if req.isResult:
                print(f"[Result] Game End for {user_id}, skipping LLM.")
                return {
                    "text": "太棒了！你完成了今天的互動練習。",
                    "b1": "再玩一次",
                    "b2": "休息一下",
                    "scene": "hugging"
                }

            print(f"[Cache] MISS: {cache_key}，即時生成中...")
            from graph.nodes import denver_model
            prompt = f"小朋友選擇了：{clean_choice}。請給予下一個情境的簡短發展。"
            final_state = await asyncio.to_thread(
                denver_model, {"user_input": prompt, "child_behavior": "遊戲互動"}
            )
            result = {
                "text": final_state.get("child_story", "處理中..."),
                "b1": final_state.get("b1", "繼續探索"),
                "b2": final_state.get("b2", "休息一下"),
                "scene": final_state.get("scene", "running")
            }

        if not req.isResult:
            start_pregen_task(user_id, result["b1"], result["b2"])

        return result
    except Exception as e:
        print(f"Error in choice: {e}")
        return JSONResponse(status_code=500, content={"message": "內部處理錯誤"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)