from datetime import datetime
from collections import deque


class CandleAnalyzer:
    def __init__(self):
        # Dictionary để lưu trữ dữ liệu cho từng symbol
        self.symbol_data = {}

    def update_candle(self, symbol, candle_data):
        """Cập nhật dữ liệu nến mới cho symbol cụ thể"""
        # Khởi tạo deque cho symbol nếu chưa tồn tại
        if symbol not in self.symbol_data:
            self.symbol_data[symbol] = {
                'candles': deque(maxlen=3),
                'count': 0
            }

        self.symbol_data[symbol]['candles'].append(candle_data)
        self.symbol_data[symbol]['count'] += 1

        if len(self.symbol_data[symbol]['candles']) == 3:
            return self.analyze_candles(symbol)
        return None

    def analyze_candles(self, symbol):
        """Phân tích logic nến trên 3 cây gần nhất cho symbol cụ thể"""
        if symbol not in self.symbol_data or len(self.symbol_data[symbol]['candles']) < 3:
            return None

        candles = self.symbol_data[symbol]['candles']
        n1, n2, n3 = list(candles)  # n1: cũ nhất, n3: mới nhất

        # Kiểm tra 2 điều kiện chính
        condition1 = self.pattern_1(n1, n2, n3)  # Logic 1
        condition2 = self.pattern_2(n1, n2, n3)  # Logic 2

        if condition1 or condition2:
            return {
                'symbol': symbol,
                'pattern1': condition1,
                'pattern2': condition2,
                'candles': [n1, n2, n3],
                'timestamp': datetime.now()
            }
        return None

    def pattern_1(self, n1, n2, n3):
        """
        Logic 1:
        - Nến 1: ĐỎ
        - Nến 2: XANH có râu trên dài
        - Nến 3: Đỏ Giá đóng cửa thấp hơn giá sàn của nến 2
        """
        # Kiểm tra nến 1 là đỏ
        if not self.is_red_candle(n1):
            return False

        # Kiểm tra nến 2 là xanh và có râu trên dài
        if not self.is_green_candle(n2):
            return False

        if not self.has_long_upper_shadow(n2):
            return False

        # Kiểm tra nến 3: giá đóng cửa thấp hơn giá sàn nến 2
        if not self.is_red_candle(n3):
            return False

        # if not self.has_long_upper_shadow(n3):
        #     return False

        if n3['low'] <= n2['low']:
            return False

        return True

    def pattern_2(self, n1, n2, n3):
        """
        Logic 2:
        - Nến 1: XANH
        - Nến 2: XANH có râu trên dài
        - Nến 3: ĐỎ và giá đóng cửa thấp hơn giá mở cửa của nến 2
        """
        # Kiểm tra nến 1 là xanh
        if not self.is_green_candle(n1):
            return False

        # Kiểm tra nến 2 là xanh và có râu trên dài
        if not self.is_green_candle(n2):
            return False

        if not self.has_long_upper_shadow(n2):
            return False

        # Kiểm tra nến 3 là đỏ và giá đóng cửa thấp hơn giá mở cửa nến 2

        if not self.is_red_candle(n3):
            return False

        # if not self.has_long_upper_shadow(n3):
        #     return False

        if n3['low'] <= n2['low']:
            return False

        return True

    def has_long_upper_shadow(self, candle):
        """Kiểm tra nến có râu trên dài"""
        if self.is_green_candle(candle):
            # Với nến xanh: râu trên = high - close
            upper_shadow = candle['high'] - candle['close']
            body = candle['close'] - candle['open']
        else:
            # Với nến đỏ: râu trên = high - open
            upper_shadow = candle['high'] - candle['open']
            body = candle['open'] - candle['close']

        # Râu trên được coi là dài khi > 60% thân nến
        if body > 0:  # Tránh chia cho 0
            return upper_shadow > (body * 0.6)
        return upper_shadow > 0

    def is_red_candle(self, candle):
        """Kiểm tra nến đỏ (giá đóng < giá mở)"""
        return candle['close'] < candle['open']

    def is_green_candle(self, candle):
        """Kiểm tra nến xanh (giá đóng > giá mở)"""
        return candle['close'] > candle['open']

    def get_symbol_info(self, symbol):
        """Lấy thông tin về symbol cụ thể"""
        if symbol in self.symbol_data:
            return {
                'candle_count': len(self.symbol_data[symbol]['candles']),
                'total_count': self.symbol_data[symbol]['count']
            }
        return {'candle_count': 0, 'total_count': 0}

    def get_all_symbols(self):
        """Lấy danh sách tất cả symbols đang được theo dõi"""
        return list(self.symbol_data.keys())

    def print_pattern_details(self, result):
        """In chi tiết về các điều kiện pattern"""
        symbol = result['symbol']
        n1, n2, n3 = result['candles']

        print(f"\n🎯 PHÁT HIỆN TÍN HIỆU - {symbol.upper()} - {datetime.now().strftime('%H:%M:%S')} 🎯")

        if result['pattern1']:
            print("🔴 PATTERN 1: Nến1(ĐỎ) → Nến2(XANH+râu trên dài) → Nến3(đóng cửa < sàn nến2)")
        if result['pattern2']:
            print("🔴 PATTERN 2: Nến1(XANH) → Nến2(XANH+râu trên dài) → Nến3(ĐỎ+đóng cửa < mở cửa nến2)")

        print("\n📈 CHI TIẾT PHÂN TÍCH:")
        if result['pattern1']:
            print("🔴 PATTERN 1:")
            print(f"   ✓ Nến1: ĐỎ (C:{n1['close']:.4f} < O:{n1['open']:.4f})")
            print(f"   ✓ Nến2: XANH + Râu trên dài (C:{n2['close']:.4f} > O:{n2['open']:.4f})")
            print(f"   ✓ Nến3: Đóng cửa {n3['close']:.4f} < Sàn nến2 {n2['low']:.4f}")
            print(f"   📉 Chênh lệch: {n2['low'] - n3['close']:.4f}")

        if result['pattern2']:
            print("🔴 PATTERN 2:")
            print(f"   ✓ Nến1: XANH (C:{n1['close']:.4f} > O:{n1['open']:.4f})")
            print(f"   ✓ Nến2: XANH + Râu trên dài (C:{n2['close']:.4f} > O:{n2['open']:.4f})")
            print(f"   ✓ Nến3: ĐỎ + Đóng cửa {n3['close']:.4f} < Mở cửa nến2 {n2['open']:.4f}")
            print(f"   📉 Chênh lệch: {n2['open'] - n3['close']:.4f}")

        print("=" * 70)

    def get_candle_info(self, candle):
        """Trả về thông tin chi tiết của nến"""
        color = "🟢 XANH" if self.is_green_candle(candle) else "🔴 ĐỎ"
        upper_shadow = candle['high'] - max(candle['open'], candle['close'])
        body = abs(candle['close'] - candle['open'])
        upper_shadow_ratio = (upper_shadow / body) if body > 0 else 0

        shadow = "✅ RÂU TRÊN DÀI" if self.has_long_upper_shadow(candle) else "❌ RÂU TRÊN NGẮN"

        return f"{color} | O:{candle['open']:.4f} H:{candle['high']:.4f} L:{candle['low']:.4f} C:{candle['close']:.4f} | {shadow} ({upper_shadow_ratio:.1%})"
