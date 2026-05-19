"""
기술적 지표 계산 엔진 (순수 pandas — 외부 라이브러리 불필요)

키움 조건식 재현에 필요한 모든 지표를 계산합니다.
"""
import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════
#  이동평균 (SMA)
# ═══════════════════════════════════════════════════════════
def sma(series: pd.Series, period: int) -> pd.Series:
    """단순 이동평균"""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수 이동평균"""
    return series.ewm(span=period, adjust=False).mean()


# ═══════════════════════════════════════════════════════════
#  이동평균 기울기 (n일 전 대비)
# ═══════════════════════════════════════════════════════════
def ma_slope(ma_series: pd.Series, lookback: int = 5) -> pd.Series:
    """이동평균의 n일 전 대비 변화 (양수=상승)"""
    return ma_series - ma_series.shift(lookback)


def ma_slope_positive(ma_series: pd.Series, lookback: int = 5) -> pd.Series:
    """이동평균이 n일 전보다 상승 중인지 (bool)"""
    return ma_series > ma_series.shift(lookback)


# ═══════════════════════════════════════════════════════════
#  골든크로스 / 데드크로스 탐지
# ═══════════════════════════════════════════════════════════
def golden_cross(price: pd.Series, ma_series: pd.Series) -> pd.Series:
    """
    가격이 이동평균을 오늘 상향 돌파 (골든크로스)
    어제: close < MA,  오늘: close >= MA
    """
    above_today = price >= ma_series
    below_yesterday = price.shift(1) < ma_series.shift(1)
    return above_today & below_yesterday


def ma_golden_cross(short_ma: pd.Series, long_ma: pd.Series) -> pd.Series:
    """단기 이평이 장기 이평을 오늘 상향 돌파"""
    cross_today = short_ma >= long_ma
    no_cross_yesterday = short_ma.shift(1) < long_ma.shift(1)
    return cross_today & no_cross_yesterday


# ═══════════════════════════════════════════════════════════
#  RSI (Relative Strength Index)
# ═══════════════════════════════════════════════════════════
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI 계산 (Wilder's smoothing 방식)
    키움 HTS와 동일한 방식
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # 첫 period일은 단순평균, 이후 지수 스무딩
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Wilder's smoothing (이후 값)
    for i in range(period, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_values = 100 - (100 / (1 + rs))
    return rsi_values


def rsi_cross_above(close: pd.Series, level: float = 70,
                    period: int = 14) -> pd.Series:
    """RSI가 오늘 특정 레벨을 상향 돌파"""
    rsi_vals = rsi(close, period)
    above_today = rsi_vals >= level
    below_yesterday = rsi_vals.shift(1) < level
    return above_today & below_yesterday


# ═══════════════════════════════════════════════════════════
#  거래량 지표
# ═══════════════════════════════════════════════════════════
def volume_spike(volume: pd.Series, period: int = 20) -> pd.Series:
    """
    거래량이 N일 이동평균 대비 상향 돌파
    오늘 거래량 > N일 평균 거래량
    """
    vol_ma = sma(volume, period)
    return volume > vol_ma


def volume_spike_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """거래량 / N일 평균 거래량 비율"""
    vol_ma = sma(volume, period)
    return volume / vol_ma.replace(0, np.nan)


def volume_long_spike(volume: pd.Series, period: int = 120) -> pd.Series:
    """장기(120일) 거래량 대비 상향 돌파"""
    vol_ma = sma(volume, period)
    return volume > vol_ma


# ═══════════════════════════════════════════════════════════
#  볼린저 밴드
# ═══════════════════════════════════════════════════════════
def bollinger_bands(close: pd.Series, period: int = 20,
                    num_std: float = 2.0):
    """
    볼린저 밴드 계산

    Returns:
        (upper, middle, lower, bandwidth, pctb)
    """
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std

    # Band Width = (upper - lower) / middle * 100
    bandwidth = ((upper - lower) / middle) * 100

    # %B = (close - lower) / (upper - lower)
    pctb = (close - lower) / (upper - lower).replace(0, np.nan)

    return upper, middle, lower, bandwidth, pctb


# ═══════════════════════════════════════════════════════════
#  이격도 (MA Deviation)
# ═══════════════════════════════════════════════════════════
def ma_deviation_pct(price: pd.Series, ma_series: pd.Series) -> pd.Series:
    """
    주가와 이동평균의 이격률 (%)
    = (price - MA) / MA * 100
    양수 = 주가가 MA 위, 음수 = 주가가 MA 아래
    """
    return ((price - ma_series) / ma_series.replace(0, np.nan)) * 100


def ma_distance_pct(ma1: pd.Series, ma2: pd.Series) -> pd.Series:
    """
    두 이평선 간 이격률 (%)
    = |ma1 - ma2| / ma2 * 100
    """
    return ((ma1 - ma2).abs() / ma2.replace(0, np.nan)) * 100


# ═══════════════════════════════════════════════════════════
#  등락률
# ═══════════════════════════════════════════════════════════
def daily_return_pct(close: pd.Series) -> pd.Series:
    """일간 등락률 (%)"""
    return close.pct_change() * 100


def intraday_return_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """당일 시가 대비 종가 등락률 (%)"""
    return ((close - open_) / open_.replace(0, np.nan)) * 100


# ═══════════════════════════════════════════════════════════
#  거래대금
# ═══════════════════════════════════════════════════════════
def trade_value(close: pd.Series, volume: pd.Series) -> pd.Series:
    """간이 거래대금 = 종가 × 거래량"""
    return close * volume
