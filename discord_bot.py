import os
import json
import re
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Literal

# 解決 Windows 終端機 Emoji/UTF-8 編碼報錯問題
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# --- Discord Bot 設定 ---
intents = discord.Intents.default()
intents.message_content = True  # 必須啟用才能讀取對話內容
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 存放各頻道/私訊的遊戲狀態 {channel_id: state_dict}
channel_game_states = {}

# --- 海龜湯預設劇本 ---
DEFAULT_STORIES = {
    "1": {
        "title": "始祖海龜湯",
        "surface": "一個男子在一家能看見海的餐廳點了一碗海龜湯。他只吃了幾口，就十分驚訝地詢問店員：這真的是海龜湯嗎？店員肯定地回答：是的，這是貨真價實的海龜湯。男子聽完後沉思了一會兒，隨後便走到餐廳外的懸崖，跳海自殺了。請問究竟發生了什麼事？",
        "truth": "該名男子在多年前曾遭遇一場嚴重的海難，與幾名同伴受困在救生艇上，在茫茫大海上漂流了許久。在彈盡糧絕、大家快要餓死之際，男子的同伴遞給他一碗肉湯，宣稱那是海龜肉煮成的湯，男子信以為真喝下了，並因此得以存活。多年後，他在這家餐廳點了真正的海龜湯，一喝之下才發現兩者的味道完全不同，進而意識到當年救了他一命的並非海龜肉，而是他因病去世的兒子（或同伴）的肉。男子無法承受自己曾吃下親人肉的殘酷真相，在極度痛苦與愧疚下選擇了跳海自殺。",
        "clues": ["主角多年前遭遇過海難", "主角在海難中吃過人肉做的湯(假海龜湯)", "主角的兒子(或妻子)在海難中喪生", "主角在餐廳吃到的才是真海龜湯，進而發現當年吃的是人肉"],
        "hints": [
            "想想男子多年前的經歷，他曾遭遇過一場災難。",
            "海難發生時，在救生艇上彈盡糧絕時，同伴給他喝了什麼？",
            "他這次在餐廳喝到的「真海龜湯」，和當年的味道有一樣嗎？",
            "味道的差異讓他意識到了殘酷的真相，與他的家人有關。"
        ]
    },
    "2": {
        "title": "廣播的秘密",
        "surface": "一個男人正在深夜的高速公路上開車。他打開了車上的廣播，聆聽稍早的節目回放。聽了幾分鐘後，他突然把車停在路邊，在車內絕望地大哭，隨後拿出一把槍結束了自己的生命。請推理事情的經過。",
        "truth": "男人是一個廣播電台的知名 DJ。兩個小時前，他在家裡謀殺了自己的妻子。為了製造「自己當時正在電台上班」的不在場證明，他事先在播音室放了一捲非常長的預錄節目帶。沒想到他開車逃逸時打開廣播回放，卻聽見廣播裡傳來錄音帶跳針卡帶的怪聲（或是完全無聲）。他意識到自己的不在場證明徹底毀了，警方馬上就會查到他頭上，絕望之下只好畏罪自殺。",
        "clues": ["主角是廣播電台 DJ", "主角謀殺了妻子", "主角預錄了廣播節目當作不在場證明", "廣播節目發生放送事故(卡帶或無聲)導致不在場證明破滅"],
        "hints": [
            "這個男人有著特殊的職業，與聲音傳遞有關。",
            "他開車走在路上，聽到了自己的聲音，但他當時明明在開車...",
            "他為什麼需要讓人以為他還在播音室？他做了什麼壞事？",
            "廣播的播放突然發生了什麼異狀，這會對他的不在場證明有何影響？"
        ]
    },
    "3": {
        "title": "引人注目的阿華",
        "surface": "阿華在超市推著購物車，沒碰到任何人，但每每個人都忍不住朝他看一眼，為什麼？",
        "truth": "阿華身上穿著新買的衣服，但衣服上的標籤（吊牌）還掛在身上忘記拿下來，所以路人才會一直盯著他看。",
        "clues": ["阿華身上穿著新衣服", "衣服上的標籤吊牌沒有拆下來", "路人看他是因為未拆的標籤"],
        "hints": [
            "可以往阿華身上穿戴、攜帶的東西去猜，而且是一件新物品。",
            "買新衣服穿出門前，通常需要做什麼處理？他似乎漏了一步。",
            "路人一直盯著他看，是因為衣服上有一個本該被剪掉的小配件。"
        ]
    },
    "4": {
        "title": "車子不見了",
        "surface": "阿豪每天上班都會把車停在公司的停車場。這天他發現車子不見了，但他最後沒有選擇報警，為什麼？",
        "truth": "他後來想起，他今天早上是搭捷運來的。",
        "clues": ["車子其實根本沒有被偷走", "阿豪今天根本沒有開車來公司", "阿豪今天是搭大眾交通工具(捷運)出門的"],
        "hints": [
            "確認一下「車子不見」這個前提，車子真的是被別人偷走的嗎？",
            "既然車子沒被偷，那它現在在哪裡？阿豪今天上班的交通方式有什麼不同？",
            "阿豪沒有開車，那他是怎麼到達公司的？這是一項常見的交通工具。"
        ]
    },
    "5": {
        "title": "沒聲音的電話",
        "surface": "阿偉的手機突然響起，但來電顯示沒有號碼，他接起來卻什麼聲音也沒有，為什麼？",
        "truth": "因為他的手機鬧鐘設定成跟電話鈴聲一樣，這只是他自己設的鬧鐘響了。",
        "clues": ["這通「電話」其實不是別人打來的", "來電顯示沒有號碼，因為那根本不是來電", "這其實是阿偉自己設定的鬧鐘"],
        "hints": [
            "這通「電話」真的是別人打來的嗎？",
            "來電顯示沒有號碼，有沒有可能它根本就不是一通電話？",
            "這東西會發出和電話一樣的鈴聲，通常用來提醒時間，那會是什麼？"
        ]
    },
    "6": {
        "title": "看不見的時鐘",
        "surface": "阿明每天都能準確地看時間，但家裡沒有時鐘，也不是看手機，他是怎麼辦到的？",
        "truth": "因為他住在學校附近，他可以透過學校的鐘聲來判斷時間。",
        "clues": ["阿明得知時間的方式是透過「聽覺」而不是視覺", "這個聲音是從阿明家外面傳來的", "阿明家附近有學校，他是聽學校的鐘聲"],
        "hints": [
            "既然家裡沒有時鐘，也不是看手機，阿明是透過其他「感官」得知時間的嗎？",
            "這個告訴他時間的線索，是從他家外面傳來的。會是什麼聲音？",
            "這個聲音每天都會有規律地響起，通常在哪種機構或建築物會有？"
        ]
    },
    "7": {
        "title": "無聲的音樂",
        "surface": "阿強打開手機準備放音樂，按下播放鍵後，手機顯示音樂正在播放，但卻完全沒有聲音，為什麼？",
        "truth": "因為他的手機連到藍芽耳機了，所以聲音是從藍芽耳機播出來的。",
        "clues": ["手機的喇叭沒有壞掉，音樂也確實在播放", "聲音其實有播放出來，只是阿強沒有聽到", "聲音是從阿強連線的藍芽耳機播出來的"],
        "hints": [
            "手機本身沒有故障，音樂也確實在播放，那聲音跑到哪裡去了？",
            "聲音其實有播出來，只是沒有從手機的喇叭出來。它傳到其他設備上了嗎？",
            "這個設備通常是戴在耳朵上的，阿強可能忘記自己有連線了。"
        ]
    },
    "8": {
        "title": "電梯裡的尖叫",
        "surface": "一名男子走進電梯，看到裡面只有一位老太太。他按下樓層鍵後，老太太突然尖叫，立刻衝出電梯。請問發生了什麼事？",
        "truth": "男子剛好穿著「電梯維修人員制服」，老太太以為電梯壞了卻還在運行，誤以為自己差點被困在危險的電梯裡，嚇得立刻逃走。",
        "clues": ["男子並沒有對老太太做任何可怕或奇怪的舉動", "老太太被男子的「外表打扮」嚇到了", "男子穿著電梯維修人員的制服，老太太以為電梯壞了"],
        "hints": [
            "男子其實甚麼事也沒做。老太太是被男子身上的某個特徵嚇到的。",
            "這個特徵跟男子的穿著打扮有關，這套衣服代表著一種職業。",
            "如果在電梯裡看到穿這種職業制服的人，通常代表這台電梯發生了甚麼事？"
        ]
    },
    "9": {
        "title": "關燈的醫生",
        "surface": "有一名醫生，晚上值班時經常在沒人的時候把病房的燈關掉，卻沒有人因此受到影響。為什麼？",
        "truth": "這名醫生是眼科醫生，專門治療夜盲症的患者。當他關掉燈，是為了測試病人是否真的能在黑暗中行動，而不是故意影響病房照明。",
        "clues": ["醫生關燈是為了進行醫療上的測試", "病房裡其實是有人的(患者)", "醫生是眼科醫生，關燈是為了測試夜盲症患者"],
        "hints": [
            "關燈這個行為，其實是醫生工作的一部分，目的是為了什麼？",
            "題目說「沒人的時候」，但其實病房裡是有人的。這個人是誰？",
            "這名醫生的專科是什麼？關燈是為了測試患者的什麼能力？"
        ]
    },
    "10": {
        "title": "不響的鬧鐘",
        "surface": "某天早上，一個男人準時起床，準備上班。但他發現鬧鐘沒有響，且電池已經沒電了。為什麼他還能準時起床？",
        "truth": "這名男子住在附近有鐵路的地方，他已經習慣每天早上某班火車經過時的聲音，因此即使鬧鐘沒響，他還是被火車的聲音準時吵醒。",
        "clues": ["男人是因為聽到特定的聲音才醒來的，而且不是鬧鐘聲", "這個聲音是從室外傳來的，且每天早上都很準時", "男人家住在鐵路附近，他是被早班火車的聲音吵醒的"],
        "hints": [
            "雖然鬧鐘沒響，但男人還是被某種聲音吵醒的。這個聲音來自哪裡？",
            "這個聲音不是人為刻意發出的，而是來自室外某種交通工具。",
            "這是一種非常準時、在固定軌道上行駛的大型交通工具。"
        ]
    }
}

class TurtleSoupResponse(BaseModel):
    answer: Literal["是", "不是", "是也不是", "方向正確，請更詳細提問", "不重要", "提問違反規則，請重新提問"] = Field(
        description="根據真相對玩家問題的標準回答，必須是這六個之一"
    )
    newly_unlocked_clues: list[str] = Field(description="玩家提問中猜中的『尚未解鎖關鍵線索』。必須與提供的線索清單字面完全相符。若無則為空陣列。")
    reason: str = Field(description="推導此回答的邏輯原因（對玩家隱藏）")

def get_gemini_llm(temperature=0.1):
    """初始化並回傳 Gemini LLM。"""
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    if not api_key:
        return None
    try:
        return ChatGoogleGenerativeAI(
            model='gemini-3.1-flash-lite',
            google_api_key=api_key,
            temperature=temperature,
            timeout=30,
            max_retries=2
        )
    except Exception as e:
        print(f"Error initializing Gemini: {e}")
        return None

async def ask_gemini_turtle_soup(question: str, surface: str, truth: str, clues: list, unlocked_clues: list) -> dict:
    """呼叫 Gemini 對玩家問題進行海龜湯判定，並同步進行對話狀態追蹤 (DST)。"""
    llm = get_gemini_llm(temperature=0.1)
    if not llm:
        return {"answer": "不重要", "reason": "Mock", "newly_unlocked_clues": []}

    clues_str = "\n".join([f"- {c}" for c in clues])
    unlocked_str = "\n".join([f"- {c}" for c in unlocked_clues]) if unlocked_clues else "無"

    system_instruction = f"""你是一個海龜湯的遊戲主持人（關主）與狀態追蹤器。
湯面：{surface}
湯底真相：{truth}

【遊戲進度追蹤 (DST)】
這碗湯的所有關鍵線索：
{clues_str}

玩家目前已解鎖的線索：
{unlocked_str}

你的任務：
1. 根據「湯底真相」回答玩家最新的問題。回答必須且只能從以下選項選擇一個：
   - 「是」：玩家猜中的細節與真相完全相符。
   - 「不是」：玩家猜錯了關鍵細節，且該細節的正確與否【會影響】推理。
   - 「是也不是」：玩家猜對了一部分，或答案取決於不同角度。
   - 「方向正確，請更詳細提問」：玩家摸到了核心邊緣，需要再進一步追問才能成為線索。
   - 「不重要」：玩家問的細節（如好不好看、物品顏色、天氣、無關配角等）對於推導出真相【完全沒有幫助或影響】，請務必選這個，以免玩家在死胡同打轉。
   - 「提問違反規則，請重新提問」：玩家的問題與遊戲無關、要求直接給答案或包含不當內容。
2. 分析玩家的問題，如果玩家的提問內容「明確猜中」了某個「尚未解鎖的關鍵線索」，請將該線索的完整文字放入 newly_unlocked_clues 陣列中。

嚴格規則：如果玩家的推論毫無根據或沒有命中核心，請勿解鎖線索。回答時保持客觀。"""

    try:
        structured_llm = llm.with_structured_output(TurtleSoupResponse, include_raw=True)
        response_dict = await structured_llm.ainvoke([
            SystemMessage(content=system_instruction),
            HumanMessage(content=question)
        ])
        parsed = response_dict.get("parsed")
        raw = response_dict.get("raw")
        
        in_tokens = raw.usage_metadata.get("input_tokens", 0) if raw and hasattr(raw, "usage_metadata") and raw.usage_metadata else 0
        out_tokens = raw.usage_metadata.get("output_tokens", 0) if raw and hasattr(raw, "usage_metadata") and raw.usage_metadata else 0
        
        return {
            "answer": parsed.answer,
            "reason": parsed.reason,
            "newly_unlocked_clues": parsed.newly_unlocked_clues,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens
        }
    except Exception as e:
        print(f"Structured Output failed: {e}")
        return {"answer": "⚠️ API 發生錯誤或被安全機制攔截，請稍後再試！", "reason": f"Failed: {e}", "newly_unlocked_clues": [], "in_tokens": 0, "out_tokens": 0}

async def get_game_hint(surface: str, truth: str, clues: list, unlocked_clues: list, story_hints: list = None) -> str:
    """直接根據目前的進度給予對應的預設提示，不再使用 AI 即時生成。"""
    if story_hints:
        progress_idx = len(unlocked_clues)
        if progress_idx < len(story_hints):
            return story_hints[progress_idx]
        else:
            return "你已經掌握了所有關鍵線索，快試著拼湊出完整的真相吧！"
    else:
        return "這是一個自訂劇本，沒有預設的進度提示喔！請發揮想像力繼續提問。"


# --- Discord 指令與事件 ---

@bot.event
async def on_ready():
    print(f"Bot has successfully started! Logged in as: {bot.user.name} ({bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="輸入 !help 查詢海龜湯玩法"))

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="🐢 AI 海龜湯關主使用指南 🐢",
        description="本機器人支援多人在此頻道一同對話解謎！",
        color=discord.Color.blue()
    )
    embed.add_field(name="📜 !menu 或 !選單", value="顯示現有的海龜湯劇本選單", inline=False)
    embed.add_field(name="🎬 !start <編號>", value="開始對應編號的海龜湯（例如：`!start 1`）", inline=False)
    embed.add_field(name="✍️ !custom 或 !自訂", value="發起私訊，引導你建立自訂的湯面與湯底", inline=False)
    embed.add_field(name="💡 !hint 或 !提示", value="獲得一個不直接劇透的神秘線索", inline=False)
    embed.add_field(name="🔓 !truth 或 !真相", value="投降並揭曉故事真相，結束本局遊戲", inline=False)
    embed.add_field(name="❓ 如何提問", value="當遊戲開始後，**直接在頻道打字提問**（不需加驚嘆號），機器人會自動判定。", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="menu", aliases=["選單"])
async def menu_cmd(ctx):
    embed = discord.Embed(
        title="🥣 可選擇的海龜湯劇本 🥣",
        description="請輸入 `!start <編號>` 或 `!開始 <編號>` 來開局！",
        color=discord.Color.green()
    )
    for num, story in DEFAULT_STORIES.items():
        embed.add_field(name=f"{num}️⃣ {story['title']}", value=f"*{story['surface'][:80]}...*", inline=False)
    embed.add_field(name="✍️ 自訂劇本", value="輸入 `!custom` 或 `!自訂` 即可建立專屬故事", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="start", aliases=["開始"])
async def start_cmd(ctx, num: str = None):
    channel_id = ctx.channel.id
    if not num:
        await ctx.send("⚠️ 請指定劇本編號！例如：`!start 1`。輸入 `!menu` 可查看選單。")
        return
        
    if num not in DEFAULT_STORIES:
        await ctx.send("⚠️ 找不到該編號的劇本！請輸入 `!menu` 確認。")
        return

    story = DEFAULT_STORIES[num]
    channel_game_states[channel_id] = {
        "status": "playing",
        "title": story["title"],
        "surface": story["surface"],
        "truth": story["truth"],
        "clues": story["clues"],
        "unlocked_clues": [],
        "asked_questions": []
    }

    embed = discord.Embed(
        title=f"🎬 遊戲開始 ——《{story['title']}》",
        description=f"**湯面：**\n{story['surface']}\n\n🔍 **本局共有 {len(story['clues'])} 個關鍵線索需要解鎖！**",
        color=discord.Color.orange()
    )
    embed.set_footer(text="💬 直接打字提問吧！輸入 !hint 拿提示，輸入 !truth 揭曉真相。")
    await ctx.send(embed=embed)

@bot.command(name="custom", aliases=["自訂"])
async def custom_cmd(ctx):
    user = ctx.author
    try:
        await user.send(
            "🐢 **開始建立你的自訂海龜湯！** 🐢\n"
            "請在此直接回覆你的【湯面】（故事描述）："
        )
        channel_game_states[user.id] = {
            "status": "custom_surface",
            "target_channel": ctx.channel.id
        }
        await ctx.send(f"📬 {user.mention} 我已私訊你，請到私訊中完成自訂步驟！")
    except discord.Forbidden:
        await ctx.send(f"⚠️ {user.mention} 無法私訊你，請至「使用者設定 ➡️ 隱私與安全」開啟「允許來自伺服器成員的私訊」。")

@bot.command(name="hint", aliases=["提示"])
async def hint_cmd(ctx):
    channel_id = ctx.channel.id
    state = channel_game_states.get(channel_id)
    if not state or state.get("status") != "playing":
        await ctx.send("⚠️ 目前頻道沒有正在進行的遊戲喔！輸入 `!menu` 挑選一個故事吧。")
        return

    # 尋找是否有對應的預設提示
    story_hints = None
    for story in DEFAULT_STORIES.values():
        if story["title"] == state.get("title"):
            story_hints = story.get("hints")
            break

    async with ctx.typing():
        hint = await get_game_hint(
            state["surface"], 
            state["truth"], 
            state["clues"], 
            state["unlocked_clues"],
            story_hints=story_hints
        )
    await ctx.send(f"💡 **神秘提示：** {hint}")

@bot.command(name="truth", aliases=["真相", "解答"])
async def truth_cmd(ctx):
    channel_id = ctx.channel.id
    state = channel_game_states.get(channel_id)
    if not state or state.get("status") != "playing":
        await ctx.send("⚠️ 目前沒有進行中的遊戲。")
        return

    embed = discord.Embed(
        title=f"🔓 揭曉真相 ——《{state['title']}》",
        description=f"**湯底（真相）：**\n{state['truth']}",
        color=discord.Color.purple()
    )
    embed.set_footer(text="🎉 感謝遊玩！輸入 !menu 可以重新選擇劇本。")
    await ctx.send(embed=embed)
    
    channel_game_states.pop(channel_id, None)


# --- 監聽所有對話（核心提問邏輯） ---

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        state = channel_game_states.get(user_id)
        
        if state and state.get("status") in ["custom_surface", "custom_truth"]:
            status = state.get("status")
            if status == "custom_surface":
                channel_game_states[user_id] = {
                    "status": "custom_truth",
                    "temp_surface": message.content,
                    "target_channel": state["target_channel"]
                }
                await message.channel.send("✅ 收到湯面！請接著輸入【湯底】（背後真相）：")
                
            elif status == "custom_truth":
                surface = state["temp_surface"]
                truth = message.content
                target_channel_id = state["target_channel"]
                
                channel_game_states[target_channel_id] = {
                    "status": "playing",
                    "title": "玩家自訂海龜湯",
                    "surface": surface,
                    "truth": truth,
                    "clues": ["玩家成功推敲出完整的真相"], # 簡易設定
                    "unlocked_clues": [],
                    "asked_questions": []
                }
                
                channel_game_states.pop(user_id, None)
                
                target_channel = bot.get_channel(target_channel_id)
                if target_channel:
                    embed = discord.Embed(
                        title="🎬 自訂遊戲開始！",
                        description=f"**湯面：**\n{surface}",
                        color=discord.Color.dark_red()
                    )
                    embed.set_footer(text="💬 直接打字提問吧！輸入 !hint 拿提示，輸入 !truth 揭曉真相。")
                    await target_channel.send(embed=embed)
                    await message.channel.send("🎉 你的自訂海龜湯已在群組頻道成功開局！")
                else:
                    await message.channel.send("⚠️ 找不到原先開局的頻道，請重新在群組輸入 `!custom`。")
            return

    channel_id = message.channel.id
    state = channel_game_states.get(channel_id)
    
    if state and state.get("status") == "playing":
        question = message.content.strip()
        if not question:
            return
            
        # 重複提問防呆與省 Token 機制
        if "asked_questions" not in state:
            state["asked_questions"] = []
            
        if question in state["asked_questions"]:
            token_total = state.get("total_in_tokens", 0) + state.get("total_out_tokens", 0)
            await message.reply(f"⚠️ **此問題剛剛已經問過囉！請嘗試其他方向。**\n*(🪙 Token 消耗: 本次 0 / 總計 {token_total})*")
            return
            
        if "total_in_tokens" not in state:
            state["total_in_tokens"] = 0
            state["total_out_tokens"] = 0

        async with message.channel.typing():
            res = await ask_gemini_turtle_soup(question, state["surface"], state["truth"], state["clues"], state["unlocked_clues"])
            answer = res.get("answer", "不重要")
            
            # 只有當 API 成功回覆時，才把問題加入快取
            if "API 發生錯誤" not in answer:
                state["asked_questions"].append(question)
                
            reason = res.get("reason", "")
            new_clues = res.get("newly_unlocked_clues", [])
            in_t = res.get("in_tokens", 0)
            out_t = res.get("out_tokens", 0)
            
            state["total_in_tokens"] += in_t
            state["total_out_tokens"] += out_t
            
        print(f"[Discord Game] Ch: {channel_id} | Q: {question} | A: {answer} | Unlocked: {new_clues}")
        
        # 處理新解鎖的線索
        for c in new_clues:
            if c in state["clues"] and c not in state["unlocked_clues"]:
                state["unlocked_clues"].append(c)
                total_clues = len(state['clues'])
                unlocked_count = len(state['unlocked_clues'])
                progress_bar = "🟩" * unlocked_count + "⬜" * (total_clues - unlocked_count)
                await message.channel.send(f"🔍 **線索解鎖！** 你獲得了關鍵情報：`{c}`\n*(目前進度：{progress_bar} )*")
        
        # 判斷是否通關
        if len(state["unlocked_clues"]) == len(state["clues"]):
            embed = discord.Embed(
                title=f"🎉 完全正確！你破解了《{state['title']}》！",
                description=f"**完整真相：**\n{state['truth']}",
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"感謝遊玩！本局總消耗 Tokens: {state['total_in_tokens'] + state['total_out_tokens']} | 輸入 !menu 重新選擇劇本。")
            await message.reply(f"**{answer}**\n\n🏆 **所有線索已集齊，遊戲結束！**", embed=embed)
            channel_game_states.pop(channel_id, None)
            return

        # 根據不同回答給予精美的表情符號與回覆
        emoji_map = {
            "是": "🟢 是",
            "不是": "🔴 不是",
            "是也不是": "🟡 是也不是",
            "方向正確，請更詳細提問": "✨ 方向正確，請更詳細提問",
            "不重要": "⚪ 不重要",
            "提問違反規則，請重新提問": "🚫 提問違反規則，請重新提問"
        }
        display_ans = emoji_map.get(answer, answer)
        token_info = f"\n*(🪙 Token 消耗: 本次 {in_t + out_t} / 總計 {state['total_in_tokens'] + state['total_out_tokens']})*"
        await message.reply(f"**{display_ans}**{token_info}")

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token or token == "your_discord_bot_token_here":
        print("❌ Error: 請在 .env 檔案中設定正確的 DISCORD_BOT_TOKEN！")
    else:
        bot.run(token)
