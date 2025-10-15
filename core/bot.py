import threading
import time
import signal
import sys
import logging
from .websocket_manager import WebSocketManager
from .message_processor import MessageProcessor
from trading.virtual_trading import VirtualTrading
from trading.monitor import OrderMonitor
from analysis.pattern_scanner import PatternScanner
from utils.logger import setup_logging
from .binance_client import BinanceOrderWatcher


class CandlePatternScannerBot:
    def __init__(self, config):
        self.config = config
        self.running = False

        # Khởi tạo các component
        self.setup_logging()
        self.virtual_trading = VirtualTrading(config)
        self.pattern_scanner = PatternScanner(config, self.virtual_trading)
        self.websocket_manager = WebSocketManager(config, self.pattern_scanner)
        self.message_processor = MessageProcessor(config, self.pattern_scanner)
        self.order_monitor = OrderMonitor(config, self.virtual_trading, self.pattern_scanner.symbols_data)
        self.binance_watcher = BinanceOrderWatcher(config)

        logging.info(f"✅ Đã khởi tạo bot với {len(self.pattern_scanner.symbols_data)} symbols")

    def setup_logging(self):
        """Thiết lập hệ thống logging"""
        setup_logging(self.config)

    def start(self):
        """Bắt đầu bot"""
        logging.info("🚀 Khởi động Candle Pattern Scanner Bot...")
        self.running = True

        # Khởi động WebSocket connections
        self.websocket_manager.start_connections()

        # Khởi động message processor
        processor_thread = threading.Thread(target=self.message_processor.start, daemon=True)
        processor_thread.start()

        # Khởi động order monitor
        monitor_thread = threading.Thread(target=self.order_monitor.start, daemon=True)
        monitor_thread.start()

        # Xử lý tín hiệu dừng
        def signal_handler(sig, frame):
            logging.info("🛑 Nhận tín hiệu dừng...")
            self.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logging.info("✅ Bot đã khởi động, đang chờ tín hiệu...")

        self.binance_watcher.twm.start()
        self.binance_watcher.twm.start_futures_user_socket(callback=self._handle_user_stream)
        threading.Thread(target=self.binance_watcher.twm.join, daemon=True).start()
        logging.info("🚀 WebSocket user stream đã khởi chạy...")

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def _handle_user_stream(self, msg):
        """Xử lý sự kiện WebSocket từ user stream"""
        if msg['e'] != 'ORDER_TRADE_UPDATE':
            return

        data = msg['o']
        symbol = data['s']
        order_id = int(data['i'])
        status = data['X']
        execution_type = data['x']
        side = data['S']
        quantity = data['q']

        # Khi lệnh entry khớp
        if status == 'FILLED' and execution_type == 'TRADE':
            logging.info(f"✅ Entry {symbol} đã khớp hoàn toàn (OrderID: {order_id})")
            logging.info(f"✅ MSG data: {data})")
            entry_price = float(data['ap'])
            if data['R']:
                self.binance_watcher.close_order(data, symbol)
            else:
                self.binance_watcher._create_tp_sl_limit_orders(
                    symbol, side, entry_price, quantity)
                # self.binance_watcher.set_active_orders(symbol, order_id, entry_price)


    def stop(self):
        """Dừng bot"""
        logging.info("⏹️ Đang dừng scanner...")
        self.running = False

        # Dừng các component
        self.websocket_manager.stop()
        self.message_processor.stop()
        self.order_monitor.stop()

        # Tổng kết trading
        self.print_summary()

    def print_summary(self):
        """In tổng kết trading"""
        summary = self.virtual_trading.get_trading_summary()
        logging.info("=" * 80)
        logging.info("📈 TỔNG KẾT TRADING:")
        logging.info(f"   Tổng lệnh: {summary['total_orders']}")
        logging.info(f"   Thắng: {summary['winning_orders']} | Thua: {summary['losing_orders']}")
        logging.info(f"   Win Rate: {summary['win_rate']:.1f}%")
        logging.info(f"   Tổng PnL: ${summary['total_pnl_usdt']:+.2f}")
        logging.info(f"   Lợi nhuận: {summary['total_percentage']:+.2f}%")
        logging.info(f"   Số dư: ${summary['current_balance']:.2f}")
        logging.info("=" * 80)

        logging.info("✅ Bot đã dừng")
