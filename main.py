from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from typing import Optional
from enum import Enum
# 导入富途所需类，新增 Session 用于区分夜盘/常规时段
from futu import OpenSecTradeContext, TrdEnv, OrderType, TrdSide, TrdMarket, Session
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
app = FastAPI(title="富途交易服务", version="2.0")

# 全局配置
API_KEY = "suiseiseki_aboard"
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111
# 本地调试默认模拟盘，夜盘测试时再改为 TrdEnv.REAL
TRADING_ENV = TrdEnv.REAL

# 接口鉴权
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
async def check_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="密钥无效")
    return api_key

# ===================== 前端传参枚举（纯字符串，FastAPI原生支持） =====================
# 买卖方向
class SideEnum(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

# 订单类型：限价/市价
class OrderTypeEnum(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

# 交易市场（仅基础市场，SDK 无 *_NIGHT）
class TradeMarketEnum(str, Enum):
    HK = "HK"
    US = "US"
    CN = "CN"

# 交易时段（区分日盘/夜盘/盘前盘后）
class SessionEnum(str, Enum):
    RTH = "RTH"         # 常规日盘（默认）
    OVERNIGHT = "OVERNIGHT"  # 美股夜盘
    ETH = "ETH"         # 盘前/盘后
    ALL = "ALL"         # 全时段

# ===================== 统一下单接口 =====================
@app.post("/api/trade/order", dependencies=[Depends(check_api_key)])
async def create_order(
    code: str,
    quantity: int,
    side: SideEnum,
    order_type: OrderTypeEnum = OrderTypeEnum.LIMIT,
    price: Optional[float] = None,
    trade_market: Optional[TradeMarketEnum] = None,
    session: Optional[SessionEnum] = None
):
    # 1. 根据股票代码自动识别市场
    if not trade_market:
        if code.startswith("HK."):
            trade_market = TradeMarketEnum.HK
        elif code.startswith("US."):
            trade_market = TradeMarketEnum.US
        elif code.startswith(("SH.", "SZ.")):
            trade_market = TradeMarketEnum.CN
        else:
            raise HTTPException(status_code=400, detail="无法识别股票代码格式")

    # 2. 规则校验：模拟盘禁止夜盘
    if TRADING_ENV == TrdEnv.SIMULATE and session == SessionEnum.OVERNIGHT:
        raise HTTPException(status_code=400, detail="模拟盘不支持夜盘功能，请切换实盘测试")

    # 3. 规则校验：夜盘仅支持限价单
    if session == SessionEnum.OVERNIGHT and order_type == OrderTypeEnum.MARKET:
        raise HTTPException(status_code=400, detail="夜盘仅支持限价单，不支持市价单")

    # 4. 规则校验：限价单必须传价格
    if order_type == OrderTypeEnum.LIMIT and price is None:
        raise HTTPException(status_code=400, detail="限价单必须传入委托价格")

    # 市价单强制价格为0
    if order_type == OrderTypeEnum.MARKET:
        price = 0.0

    # 5. 前端枚举 → 富途SDK原生枚举转换
    futu_side = TrdSide.BUY if side == SideEnum.BUY else TrdSide.SELL
    futu_order = OrderType.NORMAL if order_type == OrderTypeEnum.LIMIT else OrderType.MARKET
    futu_session = session.name

    try:
        ctx = OpenSecTradeContext(host=FUTU_HOST, port=FUTU_PORT)
        ret, data = ctx.place_order(
            price=price,
            qty=quantity,
            code=code,
            trd_side=futu_side,
            order_type=futu_order,
            trd_env=TRADING_ENV,
            session=futu_session
        )
        ctx.close()

        if ret != 0:
            raise HTTPException(status_code=400, detail=f"下单失败: {data}")

        # ========== 修复点：兼容 列表 / 单个字典 两种返回格式 ==========
        if isinstance(data, list) and len(data) > 0:
            order_info = data[0]
        else:
            order_info = data

        order_id = order_info.get("order_id", "")

        return {
            "success": True,
            "order_id": order_id,
            "stock_code": code,
            "side": side,
            "order_type": order_type,
            "trade_market": trade_market,
            "session": session
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务异常: {str(e)}")

# ===================== 持仓查询接口 =====================
@app.get("/api/account/position", dependencies=[Depends(check_api_key)])
async def get_position(
    trade_market: Optional[TradeMarketEnum] = None,
    session: Optional[SessionEnum] = None
):
    try:
        ctx = OpenSecTradeContext(host=FUTU_HOST, port=11111)
        futu_market = TrdMarket[trade_market.value] if trade_market else None
        futu_session = Session[session.value] if session else None

        ret, data = ctx.position_list_query(
            trd_env=TRADING_ENV,
            trd_market=futu_market,
            session=futu_session
        )
        ctx.close()

        if ret != 0:
            raise HTTPException(status_code=400, detail=f"查询失败: {data}")
        return {"success": True, "positions": data.to_dict("records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务异常: {str(e)}")

# ===================== 启动服务 =====================
if __name__ == "__main__":
    import uvicorn
    # 本地调试开启热重载，Linux线上部署改为 reload=False
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)