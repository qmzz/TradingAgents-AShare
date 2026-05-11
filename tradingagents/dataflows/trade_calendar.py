from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CN_TZ = ZoneInfo("Asia/Shanghai")

# 交易日历缓存文件路径（避免每次启动都网络请求）
TRADE_DATES_CACHE_FILE = Path("/app/data/trade_dates_cache.json")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def cn_today_str() -> str:
    return now_cn().date().strftime("%Y-%m-%d")


def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _save_trade_dates_cache(dates: list[date]) -> None:
    """保存交易日历到缓存文件"""
    try:
        TRADE_DATES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_DATES_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump([d.strftime("%Y-%m-%d") for d in dates], f)
    except Exception as e:
        logging.warning(f"保存交易日历缓存失败：{e}")


def _load_trade_dates_cache() -> list[date] | None:
    """从缓存文件加载交易日历"""
    try:
        if TRADE_DATES_CACHE_FILE.exists():
            with open(TRADE_DATES_CACHE_FILE, "r", encoding="utf-8") as f:
                dates_str = json.load(f)
            return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates_str]
    except Exception as e:
        logging.warning(f"加载交易日历缓存失败：{e}")
    return None


@lru_cache(maxsize=1)
def _load_cn_trade_dates() -> tuple[list[date], set[date]]:
    # 1. 先尝试从缓存文件加载（秒开）
    cached = _load_trade_dates_cache()
    if cached:
        return cached, set(cached)
    
    # 2. 缓存失效，从网络获取（带超时保护）
    try:
        import akshare as ak  # type: ignore
        
        # 设置 10 秒超时保护
        import signal
        
        def _timeout_handler(signum, frame):
            raise TimeoutError("获取交易日历超时（10 秒）")
        
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(10)
        
        try:
            df = ak.tool_trade_date_hist_sina()
        finally:
            signal.alarm(0)  # 取消超时
        
        if df is None or df.empty or "trade_date" not in df.columns:
            raise ValueError("empty trade date table")
        
        dates = sorted(
            pd_dt.date()
            for pd_dt in pd.to_datetime(df["trade_date"], errors="coerce")
            if str(pd_dt) != "NaT"
        )
        
        # 保存到缓存
        _save_trade_dates_cache(dates)
        
        return dates, set(dates)
    
    except Exception as e:
        # 记录日志但不阻塞启动
        logging.warning(f"加载交易日历失败，使用周末规则降级：{e}")
        # Fallback: no holiday calendar, only weekend rule.
        return [], set()


def is_cn_symbol(symbol: str) -> bool:
    s = symbol.strip().upper()
    return bool(re.match(r"^\d{6}(\.(SH|SZ|SS))?$", s))


def is_cn_trading_day(date_str: str) -> bool:
    d = _parse_date(date_str)
    dates, dates_set = _load_cn_trade_dates()
    if dates:
        return d in dates_set
    return d.weekday() < 5


def previous_cn_trading_day(date_str: str) -> str:
    d = _parse_date(date_str)
    dates, _ = _load_cn_trade_dates()
    if dates:
        idx = 0
        # 找到首个 >= d 的位置
        lo, hi = 0, len(dates)
        while lo < hi:
            mid = (lo + hi) // 2
            if dates[mid] < d:
                lo = mid + 1
            else:
                hi = mid
        idx = lo - 1
        if idx >= 0:
            return dates[idx].strftime("%Y-%m-%d")
    # Fallback to weekend-only rollback
    cur = d
    while True:
        cur = cur.fromordinal(cur.toordinal() - 1)
        if cur.weekday() < 5:
            return cur.strftime("%Y-%m-%d")


def cn_market_phase(now: datetime | None = None) -> str:
    now_dt = now or now_cn()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=CN_TZ)
    else:
        now_dt = now_dt.astimezone(CN_TZ)

    today = now_dt.date().strftime("%Y-%m-%d")
    if not is_cn_trading_day(today):
        return "closed"

    t = now_dt.time()
    if t < time(9, 30):
        return "pre_open"
    if time(9, 30) <= t < time(11, 30):
        return "in_session"
    if time(11, 30) <= t < time(13, 0):
        return "lunch_break"
    if time(13, 0) <= t < time(15, 0):
        return "in_session"
    return "post_close"


def cn_no_data_reason(date_str: str) -> str:
    if not is_cn_trading_day(date_str):
        return "N/A：非交易日（A股休市）"

    today = cn_today_str()
    if date_str == today:
        phase = cn_market_phase()
        if phase == "pre_open":
            return "N/A：今日尚未开盘"
        if phase in ("in_session", "lunch_break"):
            return "N/A：今日盘中，日线未收盘（可参考实时价）"
        if phase == "post_close":
            return "N/A：今日已收盘，数据源尚未更新"

    return "N/A：该交易日暂无数据（可能停牌或数据延迟）"
