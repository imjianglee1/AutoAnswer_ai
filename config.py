# ================== DeepSeek API配置 ==================

# 从 https://platform.deepseek.com/ 获取API密钥
DEEPSEEK_API_KEY = "your api key"

# DeepSeek API地址
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 模型选择：deepseek-chat(便宜+联网) / deepseek-reasoner(推理强)
DEEPSEEK_MODEL = "deepseek-chat"

# 是否启用联网搜索（True=联网搜答案更准，但略慢）
DEEPSEEK_ENABLE_SEARCH = True

# ================== 屏幕捕获配置 ==================

# 默认捕获区域（后面用工具重新框选）
CAPTURE_REGION = {
    "left": 100,
    "top": 100,
    "width": 800,
    "height": 600
}

# 快捷键
HOTKEY_SELECT = "ctrl+f1"    # 框选区域
HOTKEY_PAUSE = "ctrl+f2"     # 暂停/继续
HOTKEY_CAPTURE = "ctrl+f3"   # 手动触发一次识别
HOTKEY_EXIT = "ctrl+q"       # 退出

# 扫描间隔（秒）
SCAN_INTERVAL = 1.0

#Local Knoledge本地知识库  
