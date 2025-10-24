import threading
import time
import signal
import logging
from utils.logger import setup_logging
from .binance_client import BinanceOrderWatcher
from .order_manager import OrderBinanceManager
from .candle_analyzer import CandleAnalyzer
import queue

class CandlePatternScannerBot:
    def __init__(self, config):
        self.config = config
        self.running = False
        self.message_queue = queue.Queue()
        # Khởi tạo các component
        self.setup_logging()
        self.binance_watcher = BinanceOrderWatcher(config)
        self.symbol_scanner = {}
        self.order_manager = OrderBinanceManager(config)
        self.last_price = None
        self.prev_candle = {}

        self.analyzer = CandleAnalyzer()
        self.symbol_counters = {}
        self.position = {}
        self.get_position()
        self.counter_symbol = {}

    def get_position(self):
        positions = self.binance_watcher.client.futures_position_information()
        for p in positions:
            self.position[p["symbol"]] = p
        logging.info(f"Currenct position: {self.position}")

    def setup_logging(self):
        """Thiết lập hệ thống logging"""
        setup_logging(self.config)

    def get_symbol_stream(self):
        symbols = self.symbol_scanner.keys()
        return [f'{s.lower()}@kline_{self.config.timeframe}' for s in symbols]

    def remove_non_ascii_symbols(self, symbols):
        import re
        return [s for s in symbols if re.match(r'^[A-Za-z0-9_]+$', s)]

    def get_sigal_symbol_stream(self):
        data = self.binance_watcher.get_most_volatile_symbols(top_n=50)
        symbols = self.remove_non_ascii_symbols(data['gainers']) + self.remove_non_ascii_symbols(data['losers'])
        symbols = list(set(symbols))
        logging.info(f"Symbols: {symbols}")
        return [f'{s.lower()}@kline_{self.config.signal_time_frame}' for s in symbols]

    def start(self):
        """Bắt đầu bot"""
        self.running = True

        # Xử lý tín hiệu dừng
        def signal_handler(sig, frame):
            logging.info("🛑 Nhận tín hiệu dừng...")
            self.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logging.info("✅ Bot đã khởi động, đang chờ tín hiệu...")

        self.binance_watcher.twm.start()
        self.binance_watcher.twm.start_futures_user_socket(callback=self._handle_user_stream)
        data_streams = self.get_symbol_stream()
        signal_streams = self.get_sigal_symbol_stream()
        self.binance_watcher.twm.start_multiplex_socket(callback=self._handle_multi_kline, streams=data_streams)
        self.binance_watcher.twm.start_multiplex_socket(
            callback=self._handle_multi_signal_kline, streams=signal_streams)
        self.binance_watcher.twm.start_futures_multiplex_socket(
            callback=self._handle_mark_price,
            streams=['!markPrice@arr']
        )
        threading.Thread(target=self.binance_watcher.twm.join, daemon=True).start()
        logging.info("🚀 WebSocket user stream đã khởi chạy...")

        try:
            while self.running:
                self._handle_multi_kline_order_queue()
        except KeyboardInterrupt:
            self.stop()

    def _handle_mark_price(self, msg):
        data = [d for d in msg['data'] if d['s'] in self.position]
        for coin in data:
            symbol = coin['s']
            mark_price = float(coin['p'])
            pos = self.position.get(symbol)
            if not pos:
                continue


            if not pos:
                continue

            entry = float(pos['entryPrice'])
            amt = float(pos['positionAmt'])
            if amt == 0:
                continue

            pnl = round((mark_price - entry) * amt, 2)
            reverse_side = "SELL" if amt > 0 else "BUY"

            print(f"✅ {symbol} lãi {pnl} USDT")

            # Kiểm tra lãi
            if pnl > 0 and pnl >= 0.25:
                if not self.counter_symbol.get(symbol, False):
                    continue

                print(f"✅ {symbol} lãi {round(pnl, 2)} USDT → chốt lời")
                self.binance_watcher.close_order_tp(symbol=symbol,mark_price=mark_price * 1.001,reverse_side=reverse_side)
                del self.counter_symbol[symbol]
                continue

            if pnl < 0 and pnl <= -2.0:
                self.binance_watcher.close_order_sl(symbol, reverse_side)

            # Kiểm tra lỗ
            if pnl < 0 and pnl <= -0.20:
                print(f'counter symbol: {self.counter_symbol}')
                if symbol in self.counter_symbol and self.counter_symbol[symbol] >= 4:
                    continue

                print(f"⚠️ {symbol} đang lỗ {round(pnl, 2)} USDT → đảo chiều...")
                self.binance_watcher.close_and_reverse(symbol, reverse_side, abs(amt), reorder=True)
                if symbol in self.counter_symbol:
                    self.counter_symbol[symbol] += 1
                else:
                    self.counter_symbol[symbol] = 1
                continue


    def _handle_user_stream(self, msg):
        """Xử lý sự kiện WebSocket từ user stream"""
        if msg['e'] == 'ORDER_TRADE_UPDATE':
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
                    return
                else:
                    self.get_position()
                    if not self.counter_symbol.get(symbol):
                        self.binance_watcher._create_tp_sl_limit_orders(symbol, side, entry_price, quantity)

    def _handle_kline_signal(self, msg):
        kline = msg['k']
        if kline['x']:
            open = kline['o']
            close = kline['c']
            high = kline['h']
            low = kline['l']
            logging.info(f'Nến tín hiệu: open: {open}, close: {close}, high: {high}, low: {low}')

    def _handle_kline_order(self, msg):
        kline = msg['k']
        if kline['x']:
            open_price = kline['o']
            close_price = kline['c']
            high_price = kline['h']
            low_price = kline['l']
            logging.info(
                f'Nến đã kết thúc: open: {open_price}, close: {close_price}, high: {high_price}, low: {low_price}')

    def _handle_multi_signal_kline(self, data):
        self.message_queue.put(data)

    def _handle_multi_kline(self, data):
        if 'stream' in data:
            kline_data = data['data']['k']
            symbol = kline_data['s']
        else:
            kline_data = data['k']
            symbol = kline_data['s']
        if kline_data['x']:
            logging.info(f'Kline: {symbol} | {self.symbol_scanner}')
            if symbol in self.symbol_scanner:
                logging.info(f'Close: {symbol} in symbol_scanner')
                self.binance_watcher.close_position(symbol=symbol)
                del self.symbol_scanner[symbol]
                logging.info(f'Delete: {symbol} in symbol_scanner')

    def _handle_multi_kline_order_queue(self):
        data = self.message_queue.get()

        if 'stream' in data:
            kline_data = data['data']['k']
            symbol = kline_data['s']
        else:
            kline_data = data['k']
            symbol = kline_data['s']

        if not kline_data:
            return None

        if kline_data['x']:  # Nếu nến đã đóng
            self.process_completed_candle(symbol, kline_data)
        return None

    def get_candle_emoji(self, candle):
        """Trả về emoji cho nến"""
        if candle['close'] > candle['open']:
            return "🟢 XANH"
        else:
            return "🔴 ĐỎ"

    def process_completed_candle(self, symbol, kline):
        """Xử lý nến đã hoàn thành"""
        candle_data = {
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v']),
            'start_time': kline['t']
        }
        if candle_data['volume'] <= 100000:
            return None

        if symbol in self.symbol_counters:
            self.symbol_counters[symbol] += 1
        else:
            self.symbol_counters[symbol] = 1

        # Gửi dữ liệu nến cho analyzer
        result = self.analyzer.update_candle(symbol, candle_data)

        # Hiển thị số lượng nến đang theo dõi
        symbol_info = self.analyzer.get_symbol_info(symbol)
        # print(
        #     f"📈 Đang theo dõi: {symbol} | {symbol_info['candle_count']}/3 nến | Tổng: {symbol_info['total_count']} nến")

        # Hiển thị kết quả phân tích nếu có
        if result:
            positions = self.binance_watcher.client.futures_position_information()
            for p in positions:
                self.position[p["symbol"]] = p
            if any(p["symbol"] == symbol and abs(float(p["positionAmt"])) > 0 for p in positions):
                logging.info(f"Đã tồn tại lệnh order {symbol}")
                return

            if len(positions) >= 4:
                return

            entry_price = candle_data['close'] * 0.999
            quantity = self.order_manager.calculate_position_size(symbol=symbol, current_price=entry_price)
            # Tạo lệnh chính trên Binance
            self.binance_watcher.create_entry_order(
                symbol=symbol,
                side='BUY',
                entry_price=entry_price,
                quantity=quantity,
                # order_type='MARKET'
            )
            self.analyzer.print_pattern_details(result)

    def stop(self):
        """Dừng bot"""
        logging.info("⏹️ Đang dừng scanner...")
        self.running = False
