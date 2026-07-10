import inspect
from invisible_playwright import InvisiblePlaywright

# Trích xuất chữ ký (signature) của hàm khởi tạo
sig = inspect.signature(InvisiblePlaywright.__init__)

print("Danh sách tham số của InvisiblePlaywright:\n" + "="*45)

for name, param in sig.parameters.items():
    if name == 'self':
        continue
        
    # Kiểm tra kiểu dữ liệu (Type Hint) nếu tác giả có khai báo
    type_hint = param.annotation if param.annotation != inspect.Parameter.empty else "Không rõ"
    
    # Kiểm tra giá trị mặc định
    default_val = param.default if param.default != inspect.Parameter.empty else "Bắt buộc (Không có mặc định)"
    
    print(f"Tham số: **{name}**")
    print(f"  - Kiểu dữ liệu : {type_hint}")
    print(f"  - Mặc định     : {default_val}")
    print("-" * 45)