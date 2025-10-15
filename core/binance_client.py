import time
from binance import ThreadedWebsocketManager, Client
from .order_manager import OrderBinanceManager
import logging
from logging.handlers import RotatingFileHandler


class BinanceOrderWatcher:
    def __init__(self, config):
        """Khởi tạo client Binance và WebSocket manager"""
        self.client = Client(config.api_key, config.secret_key, testnet=config.testnet)
        self.twm = ThreadedWebsocketManager(api_key=config.api_key, api_secret=config.secret_key,
                                            testnet=config.testnet)
        self.tp_percent = config.take_profit_percentage
        self.sl_percent = config.stop_loss_percentage
        self.active_orders = {}  # symbol -> order_id
        self.order_manager = OrderBinanceManager(config)
        self.config = config
        self.leverage = config.leverage
        self.trading_logger = None
        self.setup_trading_logger()
        self.pre_order = {}

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
            exchange_info = self.order_manager._get_exchange_info()
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

    def set_active_orders(self, symbol, order_id, price):
        self.active_orders[symbol] = {'id': order_id, 'price': price, 'time': time.time()}
        logging.info(f"✅ Đã khớp {self.active_orders}")

    def close_order(self, data, symbol: str = None):
        self.client.futures_cancel_all_open_orders(symbol=symbol)
        self.trading_logger.info(
            f"ĐÓNG LỆNH | {data['s']} | "
            f"Kết quả: {'WIN' if data['ot'] == 'TAKE_PROFIT' else 'LOSS'} | "
            f"Loại: {data['ot']} | "
            f"Thời gian: {data['T']}"
        )

    def _format_price(self, symbol: str, price: float) -> float:
        """Format price theo tick size của symbol"""
        try:
            exchange_info = self.order_manager._get_exchange_info()
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

    def create_entry_order(self, symbol, side, entry_price, quantity, order_type="LIMIT", pattern=None):
        """Tạo lệnh entry (LIMIT hoặc MARKET)"""
        positions = self.client.futures_position_information()
        if any(p["symbol"] == symbol and abs(float(p["positionAmt"])) > 0 for p in positions):
            logging.info(f"Đã tồn tại lệnh order {symbol}")
            return

        logging.info(f"Order active {len(positions)}/{self.config.max_open_orders}")
        if len(positions) >= self.config.max_open_orders:
            logging.info(f"Đã đạt lệnh tối đa")
            return

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
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="LIMIT",
                price=str(entry_price),
                quantity=quantity,
                timeInForce="GTC"
            )

        self.trading_logger.info(
            f"MỞ LỆNH | {symbol} | "
            f"MÔ HÌNH: {pattern} | GÍA: {entry_price:.6f} | "
            f"Thời gian: {time.time()}"
        )
        self.pre_order[symbol] = order
        self.active_orders[symbol] = {}
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

    def _check_and_close_tp_sl(self, symbol):
        """Kiểm tra giá hiện tại so với entry, nếu chạm TP/SL thì đóng vị thế ngay lập tức"""
        positions = self.client.futures_position_information()
        if not positions:
            logging.warning(f"Không tìm thấy vị thế {symbol}")
            return
        for p in positions:
            logging.info(f'Vị thế: {p}')
            position_size = float(p["positionAmt"])
            if position_size == 0:
                logging.info(f"Không có vị thế mở cho {symbol}")
                return

            entry_price = float(p["entryPrice"])
            mark_price = float(p["markPrice"])

            side = "BUY" if position_size > 0 else "SELL"
            close_side = "SELL" if side == "BUY" else "BUY"

            rate_tp = self.config.take_profit_percentage / 100
            rate_sl = self.config.stop_loss_percentage / 100

            # Tính giá TP/SL
            if side == "BUY":  # LONG
                tp_price = entry_price * (1 + rate_tp)
                sl_price = entry_price * (1 - rate_sl)

                if mark_price >= tp_price:
                    reason = "Take Profit"
                    trigger = tp_price
                elif mark_price <= sl_price:
                    reason = "Stop Loss"
                    trigger = sl_price
                else:
                    return  # chưa chạm TP/SL
            else:  # SHORT
                tp_price = entry_price * (1 - rate_tp)
                sl_price = entry_price * (1 + rate_sl)

                if mark_price <= tp_price:
                    reason = "Take Profit"
                    trigger = tp_price
                elif mark_price >= sl_price:
                    reason = "Stop Loss"
                    trigger = sl_price
                else:
                    return  # chưa chạm TP/SL

            # --- Nếu đến đây, tức là đã chạm TP hoặc SL ---
            logging.info(f"📉 {reason} đạt cho {symbol} | Entry={entry_price} | Mark={mark_price} | Trigger={trigger}")

            # Tạo lệnh đóng vị thế ngay lập tức
            self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=abs(position_size),
                reduceOnly=True  # chỉ đóng, không mở thêm
            )

            logging.info(f"✅ Đã đóng vị thế {symbol} ({reason}) với giá {mark_price}")

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
            tp_trigger = tp_price * 1.001
            sl_trigger = sl_price * 0.999
        elif side.upper() == "SELL":
            tp_price = entry_price * (1 - rate_tp / 1000)
            sl_price = entry_price * (1 + rate_sl / 1000)
            tp_trigger = tp_price * 0.999
            sl_trigger = sl_price * 1.001
        else:
            raise ValueError("Side phải là BUY hoặc SELL")

        # --- Format giá theo quy định sàn ---
        tp_price = self._format_price(symbol, tp_price)
        sl_price = self._format_price(symbol, sl_price)
        tp_trigger = self._format_price(symbol, tp_trigger)
        sl_trigger = self._format_price(symbol, sl_trigger)

        # --- Lệnh TAKE_PROFIT_LIMIT ---
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

        # --- Lệnh STOP_LIMIT ---
        self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type="STOP",
            quantity=quantity,
            price=sl_price,
            stopPrice=sl_trigger,
            timeInForce="GTC",
            workingType="MARK_PRICE",
            reduceOnly=True
        )

        logging.info(f"📡 Đã gửi lệnh TP/SL (LIMIT) cho {symbol} - TP: {tp_price}, SL: {sl_price}, Quantity: {quantity}")

    def stop(self):
        """Dừng WebSocket"""
        self.twm.stop()
        logging.info("🛑 Đã dừng WebSocket")
