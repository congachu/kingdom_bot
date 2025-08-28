# utils/constants.py
from zoneinfo import ZoneInfo

# NPC 고정 비율
NPC_RESOURCE_RATE = 0.65  # 자원 NPC 매입률 (고정)
NPC_ITEM_RATE     = 0.95  # 아이템 NPC 매입률 (고정)
NPC_ITEM_TAX      = 0.05  # 아이템 NPC 매입세 (고정) → 국고 귀속

# 시세 평활화
EMA_ALPHA = 0.20  # 0.0~1.0

# 초깃값
INITIAL_TREASURY = 50_000

# 토지 기본값
def land_defaults(tier: int) -> tuple[int, int]:
    # (일일 생산량, 주간 유지비)
    return (2, 200) if tier == 1 else (3, 400) if tier == 2 else (4, 800)

# 드랍 테이블(편향 전)
BASE_DROP = [("iron",25), ("wood",25), ("stone",25), ("herb",15), ("water",10)]
RESOURCE_TYPES = ["iron", "wood", "stone", "herb", "water"]
