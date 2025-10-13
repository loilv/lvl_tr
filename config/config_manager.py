import yaml
import logging


class Config:
    def __init__(self, config_path='config/config.yaml'):
        self.config_path = config_path
        self.load_config()

    def load_config(self):
        """Tải cấu hình từ file YAML"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as file:
                config_data = yaml.safe_load(file)

            self.symbols = config_data.get('symbols', [])
            self.timeframe = config_data.get('timeframe', '5m')
            self.websocket_base_url = config_data.get('websocket', {}).get('base_url', 'wss://stream.binance.com:9443')
            self.reconnect_delay = config_data.get('websocket', {}).get('reconnect_delay', 5)

            # Cấu hình logging
            logging_config = config_data.get('logging', {})
            self.log_level = logging_config.get('level', 'INFO')
            self.log_file = logging_config.get('log_file', 'logs/candle_scanner.log')
            self.signals_log_file = logging_config.get('signals_log_file', 'logs/trading_signals.log')
            self.trading_log_file = logging_config.get('trading_log_file', 'logs/trading_results.log')
            self.max_file_size = logging_config.get('max_file_size_mb', 100) * 1024 * 1024
            self.backup_count = logging_config.get('backup_count', 5)

            # Cấu hình phân tích
            analysis_config = config_data.get('analysis', {})
            self.update_interval = analysis_config.get('update_interval', 1.0)
            self.max_symbols = analysis_config.get('max_symbols_per_connection', 200)
            self.scan_all_pairs = analysis_config.get('scan_all_pairs', True)

            # Cấu hình trading
            trading_config = config_data.get('trading', {})
            self.risk_reward_ratio = trading_config.get('risk_reward_ratio', 1.5)
            self.stop_loss_percentage = trading_config.get('stop_loss_percentage', 1.0)
            self.take_profit_percentage = trading_config.get('take_profit_percentage', 1.5)
            self.entry_strategy = trading_config.get('entry_strategy', 'counter_trend')
            self.entry_price_offset = trading_config.get('entry_price_offset', 0.05)
            self.min_volume = trading_config.get('min_volume', 50000)
            self.volume_spike_threshold = trading_config.get('volume_spike_threshold', 1.5)
            self.account_balance = trading_config.get('account_balance', 1000)
            self.leverage = trading_config.get('leverage', 10)
            self.position_size_usdt = trading_config.get('position_size_usdt', 100)
            self.virtual_trading = trading_config.get('virtual_trading', True)
            self.max_open_orders = trading_config.get('max_open_orders', 10)
            # Binance
            binance_config = config_data.get('binance', {})
            self.api_key = binance_config.get('api_key', '')
            self.secret_key = binance_config.get('secret_key', '')
            self.base_url = binance_config.get('base_url', '')

            # Cấu hình mô hình nến
            self.enabled_bearish_patterns = trading_config.get('enabled_patterns', {}).get('bearish', [])
            self.enabled_bullish_patterns = trading_config.get('enabled_patterns', {}).get('bullish', [])

            print(f"✅ Đã tải cấu hình: {len(self.enabled_bearish_patterns)} mô hình giảm, {len(self.enabled_bullish_patterns)} mô hình tăng")
            print(f"💰 Trading: Balance=${self.account_balance}, Position Size=${self.position_size_usdt}, Leverage={self.leverage}x")

        except Exception as e:
            print(f"❌ Lỗi tải file cấu hình: {e}")