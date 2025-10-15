import json
import queue
import time
import logging
import requests
from .symbol_data import SymbolData
from trading.calculator import TradingCalculator
from core.order_manager import OrderBinanceManager
from core.binance_client import BinanceOrderWatcher


class PatternScanner:
    def __init__(self, config, virtual_trading):
        self.config = config
        self.virtual_trading = virtual_trading
        self.max_symbols = config.max_symbols
        self.symbols_data = {}
        self.message_queue = queue.Queue()
        self.signals_logger = logging.getLogger('signals')
        self.trading_calculator = TradingCalculator(config)
        self.order_manager = OrderBinanceManager(config)
        self.binance_client = BinanceOrderWatcher(config)

        # Theo dõi các lệnh đang chờ đặt TP/SL
        self.pending_orders = {}  # {order_id: order_info}
        self.filled_orders = {}  # {order_id: order_info}

        # Tải symbols
        if config.scan_all_pairs:
            self.load_all_usdt_pairs()
        else:
            for symbol in config.symbols:
                self.symbols_data[symbol] = SymbolData(symbol, config.timeframe)

        logging.info(f"✅ Đã khởi tạo scanner với {len(self.symbols_data)} symbols")

    def load_all_usdt_pairs(self):
        """Tải tất cả các cặp USDT từ Binance"""
        try:
            response = requests.get('https://api.binance.com/api/v3/exchangeInfo', timeout=10)
            data = response.json()

            usdt_pairs = []
            for symbol_info in data['symbols']:
                if symbol_info['status'] == 'TRADING' and symbol_info['quoteAsset'] == 'USDT':
                    usdt_pairs.append(symbol_info['symbol'])

            max_pairs = self.max_symbols
            if len(usdt_pairs) > max_pairs:
                usdt_pairs = usdt_pairs[:max_pairs]

            for symbol in usdt_pairs:
                self.symbols_data[symbol] = SymbolData(symbol, self.config.timeframe)

            logging.info(f"📊 Đã tải {len(usdt_pairs)} cặp USDT")

        except Exception as e:
            logging.info(f"❌ Lỗi tải danh sách cặp: {e}")
            test_pairs = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'DOGEUSDT', 'SOLUSDT']
            for symbol in test_pairs:
                self.symbols_data[symbol] = SymbolData(symbol, self.config.timeframe)
            logging.info(f"📊 Sử dụng {len(test_pairs)} cặp test")

    def process_message(self, message):
        """Xử lý tin nhắn từ WebSocket"""
        try:
            data = json.loads(message)
            if 'stream' in data:
                kline_data = data['data']['k']
                symbol = kline_data['s']
            else:
                kline_data = data['k']
                symbol = kline_data['s']

            if symbol in self.symbols_data:
                symbol_data = self.symbols_data[symbol]

                # ĐẢM BẢO chuyển đổi kiểu dữ liệu
                try:
                    current_price = float(kline_data['c'])
                    symbol_data.current_price = current_price
                except (ValueError, TypeError) as e:
                    logging.info(f"Lỗi chuyển đổi giá {symbol}: {e}")
                    return

                # Kiểm tra lệnh mở
                if symbol in self.virtual_trading.open_orders:
                    result, close_price = self.virtual_trading.check_order_conditions(symbol, current_price)
                    if result:
                        order, message = self.virtual_trading.close_order(symbol, close_price, result)
                        if order:
                            if result == "WIN":
                                logging.info(
                                    f"🎉 THẮNG LỆNH {symbol} | {order.pattern} | PnL: {order.pnl_percentage:+.2f}% (${order.pnl_usdt:+.2f})")
                            else:
                                logging.info(
                                    f"😞 THUA LỆNH {symbol} | {order.pattern} | PnL: {order.pnl_percentage:+.2f}% (${order.pnl_usdt:+.2f})")

                # Xử lý nến đóng
                if kline_data['x']:
                    self.process_completed_candle(symbol, symbol_data, kline_data)

        except Exception as e:
            logging.info(f"Lỗi xử lý message: {e}")

    def process_completed_candle(self, symbol, symbol_data, kline_data):
        """Xử lý khi nến hoàn thành"""
        try:
            # ĐẢM BẢO chuyển đổi kiểu dữ liệu cho tất cả giá trị
            open_price = float(kline_data['o'])
            close_price = float(kline_data['c'])
            high_price = float(kline_data['h'])
            low_price = float(kline_data['l'])
            volume = float(kline_data['v'])  # QUAN TRỌNG: chuyển volume sang float

            symbol_data.update_volume_history(volume)
            symbol_data.update_higher_timeframe_open_price(kline_data['t'], open_price)

            # Phân tích mô hình nến
            pattern = self.analyze_candlestick_patterns(symbol_data, open_price, close_price, high_price, low_price)

            # Lưu nến hiện tại
            symbol_data.prev_candle = (open_price, close_price)

            if pattern and self.config.virtual_trading:
                logging.info(f"🔍 Phát hiện tín hiệu {pattern} trên {symbol}")

                is_valid, validation_message = self.trading_calculator.validate_signal(
                    volume, symbol_data.average_volume, pattern
                )

                if not is_valid:
                    logging.info(f"❌ Tín hiệu {symbol} không hợp lệ: {validation_message}")
                    return

                # Xác định loại lệnh
                if pattern in self.config.enabled_bullish_patterns:
                    order_type = "BUY"
                elif pattern in self.config.enabled_bearish_patterns:
                    order_type = "SELL"
                else:
                    return

                # Tính toán parameters với xử lý lỗi
                try:
                    entry_price = self.trading_calculator.calculate_entry_price(close_price, pattern)
                except Exception as e:
                    logging.info(f"❌ Lỗi tính toán parameters {symbol}: {e}")
                    return

                # Tính toán quantity cho Binance
                quantity = self.order_manager.calculate_position_size(symbol=symbol, current_price=entry_price)

                # Tạo lệnh chính trên Binance
                self.binance_client.create_entry_order(
                    symbol=symbol,
                    side=order_type,
                    entry_price=entry_price,
                    quantity=quantity,
                    pattern=pattern
                )

        except Exception as e:
            logging.info(f"❌ Lỗi xử lý nến {symbol}: {e}")

    def analyze_candlestick_patterns(self, symbol_data, open_price, close_price, high_price, low_price):
        """Phân tích mô hình nến"""
        try:
            total_range = high_price - low_price
            if total_range <= 0:
                return None

            body_size = abs(close_price - open_price)
            upper_shadow = high_price - max(open_price, close_price)
            lower_shadow = min(open_price, close_price) - low_price

            upper_shadow_ratio = upper_shadow / total_range
            lower_shadow_ratio = lower_shadow / total_range
            body_ratio = body_size / total_range

            is_bullish = close_price > open_price * 1.001
            is_bearish = close_price < open_price * 0.999

            prev_candle = getattr(symbol_data, "prev_candle", None)

            # --- Bearish patterns ---
            if "SHOOTING_STAR" in self.config.enabled_bearish_patterns and \
                    is_bearish and upper_shadow >= body_size * 1.5 and \
                    upper_shadow_ratio >= 0.4 and lower_shadow_ratio <= 0.2:
                return "SHOOTING_STAR"

            if "HANGING_MAN" in self.config.enabled_bearish_patterns and \
                    lower_shadow >= body_size * 1.5 and \
                    lower_shadow_ratio >= 0.4 and upper_shadow_ratio <= 0.2:
                return "HANGING_MAN"

            if "BEARISH_ENGULFING" in self.config.enabled_bearish_patterns and prev_candle:
                prev_open, prev_close = prev_candle
                if is_bearish and prev_close > prev_open and \
                        open_price > prev_close and close_price < prev_open:
                    return "BEARISH_ENGULFING"

            # --- Bullish patterns ---
            if "HAMMER" in self.config.enabled_bullish_patterns and \
                    lower_shadow >= body_size * 1.5 and \
                    lower_shadow_ratio >= 0.4 and upper_shadow_ratio <= 0.2:
                return "HAMMER"

            if "INVERTED_HAMMER" in self.config.enabled_bullish_patterns and \
                    upper_shadow >= body_size * 1.5 and \
                    upper_shadow_ratio >= 0.4 and lower_shadow_ratio <= 0.2:
                return "INVERTED_HAMMER"

            if "BULLISH_ENGULFING" in self.config.enabled_bullish_patterns and prev_candle:
                prev_open, prev_close = prev_candle
                if is_bullish and prev_close < prev_open and \
                        open_price < prev_close and close_price > prev_open:
                    return "BULLISH_ENGULFING"

            return None

        except Exception as e:
            logging.exception(f"Lỗi phân tích mô hình nến: {e}")
            return None