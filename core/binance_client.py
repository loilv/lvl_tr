import time
from binance import ThreadedWebsocketManager, Client
import logging
import queue
from logging.handlers import RotatingFileHandler


class BinanceOrderWatcher:
    def __init__(self, config):
        """Khởi tạo client Binance và WebSocket manager"""
        self.client = Client(config.api_key, config.secret_key, testnet=config.testnet)
        self.twm = ThreadedWebsocketManager(api_key=config.api_key, api_secret=config.secret_key,
                                            testnet=config.testnet)
        self.active_orders = {}  # symbol -> order_id
        self.config = config
        self.trading_logger = None
        self.setup_trading_logger()
        self.pre_order = {}
        self.message_queue = queue.Queue()
        self.leverage = config.leverage

    def get_most_volatile_symbols(self, top_n=50, min_volume_usdt=10_000_000):
        """
        Lấy 50 coin Futures có biến động mạnh nhất (tăng/giảm)
        và khối lượng giao dịch lớn nhất trong 24h.
        """
        tickers = self.client.futures_ticker()

        # Lọc chỉ lấy các cặp USDT (loại bỏ coin margin)
        tickers = [t for t in tickers if t['symbol'].endswith('USDT')]

        processed = []
        for t in tickers:
            try:
                change = float(t.get('priceChangePercent', 0))
                volume = float(t.get('volume', 0))
                last_price = float(t.get('lastPrice', 0))

                # Tính volume theo USDT (giá * khối lượng)
                volume_usdt = volume * last_price

                # Bỏ qua coin volume thấp
                if volume_usdt < min_volume_usdt:
                    continue

                processed.append({
                    'symbol': t['symbol'],
                    'priceChangePercent': change,
                    'volume_usdt': volume_usdt
                })
            except Exception:
                continue

        # Sắp xếp theo biến động %
        top_gainers = sorted(processed, key=lambda x: x['priceChangePercent'], reverse=True)[:top_n]
        top_losers = sorted(processed, key=lambda x: x['priceChangePercent'])[:top_n]

        return {
            "gainers": [t['symbol'] for t in top_gainers],
            "losers": [t['symbol'] for t in top_losers]
        }

    def setup_trading_logger(self):
        """Thiết lập logger cho trading"""
        self.trading_logger = logging.getLogger('trading')
        self.trading_logger.setLevel(logging.INFO)
        trading_handler = RotatingFileHandler(
            self.config.trading_log_file,
            maxBytes=self.config.max_file_size,
            backupCount=self.config.backup_count,
            encoding='utf-8'
        )
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        trading_handler.setFormatter(formatter)
        self.trading_logger.addHandler(trading_handler)
        self.trading_logger.propagate = False

    def _format_quantity(self, symbol: str, quantity: float) -> float:
        """Format quantity theo step size của symbol"""
        try:
            # Lấy thông tin symbol từ exchange info
            exchange_info = self.client.futures_exchange_info()
            if not exchange_info:
                return round(quantity, 3)

            for symbol_info in exchange_info.get('symbols', []):
                if symbol_info['symbol'] == symbol:
                    filters = symbol_info.get('filters', [])
                    for filter_info in filters:
                        if filter_info['filterType'] == 'LOT_SIZE':
                            step_size = float(filter_info['stepSize'])
                            formatted_qty = round(quantity / step_size) * step_size
                            return round(formatted_qty, 8)

            return round(quantity, 3)

        except Exception as e:
            return round(quantity, 3)

    def _format_price(self, symbol: str, price: float) -> float:
        """Format price theo tick size của symbol"""
        try:
            exchange_info = self.client.futures_exchange_info()
            if not exchange_info:
                return round(price, 2)

            for symbol_info in exchange_info.get('symbols', []):
                if symbol_info['symbol'] == symbol:
                    filters = symbol_info.get('filters', [])
                    for filter_info in filters:
                        if filter_info['filterType'] == 'PRICE_FILTER':
                            tick_size = float(filter_info['tickSize'])
                            formatted_price = round(price / tick_size) * tick_size
                            return round(formatted_price, 8)

            return round(price, 2)

        except Exception as e:
            return round(price, 2)

    def close_order_tp(self, symbol, mark_price, reverse_side):
        entry_price = self._format_price(symbol, mark_price)
        self.client.futures_create_order(
            symbol=symbol,
            side=reverse_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=entry_price,
            closePosition=True
        )

    def close_order_sl(self, symbol, reverse_side):
        self.client.futures_create_order(
            symbol=symbol,
            side=reverse_side,
            type="MARKET",
            closePosition=True
        )

    def close_position(self, symbol):
        position_info = self.client.futures_position_information(symbol=symbol)
        position_amt = float(position_info[0]['positionAmt'])

        # Nếu có vị thế thì đóng
        if position_amt != 0:
            side = "SELL" if position_amt > 0 else "BUY"  # Nếu đang LONG thì SELL để đóng, nếu SHORT thì BUY để đóng
            quantity = abs(position_amt)

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=True  # ⚡ đảm bảo chỉ đóng vị thế, không mở thêm
            )

            logging.info("✅ Đã đóng lệnh Market:", order)
        else:
            logging.info("⚠️ Không có vị thế mở để đóng.")

    def create_entry_order(self, symbol, side, entry_price, quantity, order_type="LIMIT", pattern=None):
        """Tạo lệnh entry (LIMIT hoặc MARKET)"""

        self.client.futures_change_leverage(symbol=symbol, leverage=self.leverage)
        logging.info(f"🟢 Gửi lệnh {order_type} {side} {symbol} tại {entry_price}")
        entry_price = self._format_price(symbol, entry_price)
        quantity = self._format_quantity(symbol, quantity)

        if order_type == "MARKET":
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity
            )
        else:
            logging.info(quantity)
            logging.info({
                'symbol': symbol,
                'side': side,
                'type': "LIMIT",
                'price': entry_price,
                'quantity': quantity,
                'timeInForce': "GTC"
            })
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="LIMIT",
                price=entry_price,
                quantity=quantity,
                timeInForce="GTC"
            )

        self.trading_logger.info(
            f"MỞ LỆNH | {symbol} | "
            f"Thời gian: {time.time()}"
        )
        return order

    def _calculate_tp_sl(self, entry_price, side):
        """Tính giá TP/SL dựa theo %"""
        roi_tp = self.config.take_profit_percentage / 100
        roi_sl = self.config.stop_loss_percentage / 100
        delta_percent_tp = roi_tp / self.leverage  # đổi ROI thành % thay đổi giá
        delta_percent_sl = roi_sl / self.leverage  # đổi ROI thành % thay đổi giá
        if side == "BUY":
            tp = entry_price * (1 + delta_percent_tp)
            sl = entry_price * (1 - delta_percent_sl)
        else:
            tp = entry_price * (1 - delta_percent_tp)
            sl = entry_price * (1 + delta_percent_sl)
        return tp, sl

    def _create_tp_sl_orders(self, symbol, side, tp_price, sl_price):
        """Tạo lệnh TP/SL"""
        opposite_side = "SELL" if side == "BUY" else "BUY"
        tp_price = self._format_price(symbol, tp_price)
        sl_price = self._format_price(symbol, sl_price)

        # TP
        self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True
        )
        # SL
        self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True
        )
        logging.info(f"📡 Đã gửi lệnh TP/SL cho {symbol}")

    def close_and_reverse(self, symbol, side, qty, reorder=False):
        """Đóng lệnh hiện tại và mở lệnh ngược chiều."""
        try:
            # 1️⃣ Đóng lệnh hiện tại
            opposite_side = "SELL" if side == "BUY" else "BUY"
            self.client.futures_create_order(
                symbol=symbol,
                side=opposite_side,
                type="MARKET",
                quantity=abs(qty)
            )
            print(f"Đã đóng vị thế {side} {qty} {symbol}")

            if reorder:
                # 2️⃣ Mở lệnh ngược lại
                quantity = self._format_quantity(symbol, qty)
                self.client.futures_create_order(
                    symbol=symbol,
                    side=opposite_side,
                    type="MARKET",
                    quantity=quantity
                )
                print(f"Đã mở vị thế ngược lại ({side}) {qty} {symbol}")

        except Exception as e:
            print(f"Lỗi khi đảo chiều: {e}")

    def _create_tp_sl_limit_orders(self, symbol, side, entry_price, quantity):
        """
        Tạo lệnh TP/SL dạng LIMIT dựa theo % lợi nhuận/lỗ theo vốn bỏ vào
        symbol: cặp giao dịch
        side: "BUY" hoặc "SELL"
        entry_price: giá vào lệnh
        capital: số USDT bỏ vào lệnh
        leverage: đòn bẩy
        """
        opposite_side = "SELL" if side == "BUY" else "BUY"
        rate_tp = round(self.config.take_profit_percentage / self.config.position_size_usdt, 2)
        rate_sl = round(self.config.stop_loss_percentage / self.config.position_size_usdt, 2)
        # tp = entry_price * (1 + rate / 1000)

        # --- Tính giá TP/SL dựa trên vốn ---
        quantity = float(quantity)
        if side.upper() == "BUY":
            tp_price = entry_price * (1 + rate_tp / 1000)
            sl_price = entry_price * (1 - rate_sl / 1000)
            tp_trigger = tp_price * 0.999
            sl_trigger = sl_price * 1.001
        elif side.upper() == "SELL":
            tp_price = entry_price * (1 - rate_tp / 1000)
            sl_price = entry_price * (1 + rate_sl / 1000)
            tp_trigger = tp_price * 1.001
            sl_trigger = sl_price * 0.999
        else:
            raise ValueError("Side phải là BUY hoặc SELL")

        # --- Format giá theo quy định sàn ---
        tp_price = self._format_price(symbol, tp_price)
        sl_price = self._format_price(symbol, sl_price)
        tp_trigger = self._format_price(symbol, tp_trigger)
        sl_trigger = self._format_price(symbol, sl_trigger)

        # --- Lệnh TAKE_PROFIT_LIMIT ---
        # self.client.futures_create_order(
        #     symbol=symbol,
        #     side=opposite_side,
        #     type="TAKE_PROFIT_MARKET",
        #     stopPrice=tp_price,
        #     closePosition=True
        # )

        self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type="TAKE_PROFIT",
            quantity=quantity,
            price=tp_price,
            stopPrice=tp_trigger,
            timeInForce="GTC",
            workingType="MARK_PRICE",
            reduceOnly=True
        )

        # # --- Lệnh STOP_LIMIT ---
        # self.client.futures_create_order(
        #     symbol=symbol,
        #     side=opposite_side,
        #     type="STOP",
        #     quantity=quantity,
        #     price=sl_price,
        #     stopPrice=sl_trigger,
        #     timeInForce="GTC",
        #     workingType="MARK_PRICE",
        #     reduceOnly=True
        # )

        logging.info(f"📡 Đã gửi lệnh TP/SL (LIMIT) cho {symbol} - TP: {tp_price}")

    def stop(self):
        """Dừng WebSocket"""
        self.twm.stop()
        logging.info("🛑 Đã dừng WebSocket")
