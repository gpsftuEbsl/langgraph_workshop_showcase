import json
import urllib.parse
import os
import uuid
import asyncio
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from graph.main import build_graph 

# --- 全域變數 ---
game_storage = {}  # 存放遊戲初始劇本 {id: data}
choice_cache = {}  # 存放預生成的下一步結果 {f"{userId}:{choice}": result}
user_tasks = {}    # 存放每個使用者正在跑的背景任務 {userId: [asyncio.Task]}

app = FastAPI(title="Early Intervention Agent API - Phase 2")

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

# --- 階段二核心：臨時記憶體資料庫 ---
# game_storage 格式: { "uuid-123": { "text": "...", "b1": "...", "b2": "..." } }
# choice_cache 格式: { "uuid-123:選項文字": { "text": ..., "b1": ..., "b2": ..., "scene": ... } }
game_storage = {}
choice_cache = {}

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

# --- API 實作: analyze、get_game、choice ---

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
    
    # 這裡加入日誌，方便除錯
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
        # 呼叫 LangGraph 大腦
        final_state = graph.invoke({"user_input": req.message})
        
        if final_state.get("error"):
            return {"reply_text": final_state.get("error")}

        behavior = final_state.get("child_behavior", "未知行為")
        parent_advice = final_state.get('parent_advice', '大腦思考中...')
        child_story = final_state.get('child_story', '系統處理中...')
        
        # 1. 準備給孩子的遊戲劇本內容
        game_data = {
            "text": child_story,
            "b1": final_state.get("b1", "繼續探索"),
            "b2": final_state.get("b2", "休息一下"),
            "scene": final_state.get("scene", "running") # 讓 LLM 決定圖片名稱
            #TODO: 增加其他情境圖片，並把圖片放在前端的資料夾
        }

        # 2. 產生唯一 ID 並存入記憶體
        game_id = str(uuid.uuid4())[:8]
        game_storage[game_id] = game_data

        # 3. 背景立刻開始預生成，並清理舊任務
        start_pregen_task(req.user_id, game_data["b1"], game_data["b2"])

        # 取得危急等級與格式化
        level = final_state.get("criticality_level", "Medium")
        level_map = {"Low": "🟢低", "Medium": "🟡未知", "High": "🔴高"}
        level_display = level_map.get(level, level)

        # 3. 產生帶有 id 與 uid 的網址 (方便未來 LINE 推播辨識)
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
    """
    第二步：前端網頁載入時，憑 id 來這裡拿劇本 JSON
    """
    data = game_storage.get(id)
    if not data:
        return JSONResponse(status_code=404, content={"message": "找不到該遊戲紀錄"})

    return data


@app.post("/api/choice")
async def handle_game_choice(req: ChoiceRequest, background_tasks: BackgroundTasks):
    """
    第三步：處理玩家點擊按鈕後的邏輯。
    優先命中預快取（<100ms），快取 miss 才即時呼叫 LLM（fallback）。
    """
    try:
        user_id = req.userId or "anonymous"
        
        # 🚀 借殼上市：如果是檢查快取請求
        if req.isCheck:
            b1_ready = f"{user_id}:{req.b1.strip()}" in choice_cache
            b2_ready = f"{user_id}:{req.b2.strip()}" in choice_cache
            if not (b1_ready and b2_ready):
                print(f"[Check-via-POST] {user_id} | b1:{req.b1.strip()}({b1_ready}) | b2:{req.b2.strip()}({b2_ready})")
            return { "b1_ready": b1_ready, "b2_ready": b2_ready }

        # 原本的選擇處理邏輯
        clean_choice = req.choice.strip()
        cache_key = f"{user_id}:{clean_choice}"
        cached = choice_cache.pop(cache_key, None)

        if cached:
            print(f"[Cache] HIT: {cache_key}")
            result = cached
        else:
            # 🚀 再次攔截：如果是結果頁面且快取沒中，直接回傳預設值，不准叫 Gemini
            if req.isResult:
                print(f"[Result] Game End for {user_id}, skipping LLM.")
                return {
                    "text": "太棒了！你完成了今天的互動練習。",
                    "b1": "再玩一次",
                    "b2": "休息一下",
                    "scene": "hugging"
                }

            # Fallback：即時呼叫 LLM
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

        # 拿到結果後，立刻在背景啟動「下下一步」的預生成
        if not req.isResult:
            start_pregen_task(user_id, result["b1"], result["b2"])

        return result
    except Exception as e:
        print(f"Error in choice: {e}")
        return JSONResponse(status_code=500, content={"message": "內部處理錯誤"})

if __name__ == "__main__":
    import uvicorn
    # 注意：reload=True 在開發階段很好用，會自動偵測存檔重啟
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)