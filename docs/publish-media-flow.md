# Luồng đăng ảnh/video qua invisible_playwright

## Mục tiêu

- Luôn đăng nhập và mở `https://www.tiktok.com/foryou?lang=en` trước.
- Chỉ chuyển sang TikTok Studio khi `/foryou` ổn định trong hai lần kiểm tra liên tiếp.
- Nếu đường dẫn ảnh chứa ít nhất một ảnh hợp lệ, đăng ảnh. Video chỉ là phương án dự phòng.
- Không quét/click hàng loạt DOM; chỉ thao tác các control cần thiết bằng Playwright locator.
- Không báo thành công nếu TikTok chưa xác nhận.

## Kết quả trải nghiệm trực tiếp (25/08/2026)

Bài kiểm tra dùng chính `InvisiblePlaywrightAdapter`, proxy/seed/cookie của một account rảnh và dừng ở preview:

1. `/foryou` tải `document.readyState=complete`, có feed, không có nút login và không có CAPTCHA.
2. `prepare_foryou_home()` trả `true`.
3. TikTok Studio có hai tab role=`tab`: `Videos` và `Photos`.
4. Tab Videos có một input ẩn: `accept="video/*"`, `multiple=false`.
5. Sau click tab Photos, input đổi thành:
   `accept="image/jpg,image/jpeg,image/png,image/webp"`, `multiple=true`.
6. Một file PNG test được đưa qua hộp thoại Windows thật; editor sẵn sàng và nút Post enabled.
7. Bài test không click Post, không đăng nội dung ra tài khoản.

### Kiểm tra nút Post dưới cuối trang

TikTok Studio đặt form trong một `DIV` cuộn riêng, không phải scroll root của trang.
Số đo thực tế với ảnh preview:

- trước cuộn: nút Post ở `y=1175..1211`, ngoài viewport cao `1067`;
- container: `clientHeight=999`, `scrollHeight=1175`, `scrollTop=0`;
- sau mouse-wheel trên đúng container: `scrollTop=176`;
- nút Post chuyển vào `y=999..1035`, nằm trọn trong viewport và vẫn enabled.

Không dùng `scroll_into_view_if_needed()` cho bước này vì patched Firefox timeout
trên nút nằm ngoài vùng cuộn lồng. Adapter di chuyển chuột vào container, cuộn bánh
xe theo nhịp humanized, re-resolve nút visible, rồi xác nhận bounding box trước click.

### Popup riêng của video

Sau khi video xử lý xong, Studio có thể mở modal `Turn on automatic content checks?`
với hai nút `Cancel` và `Turn on`. Adapter chỉ click `Cancel` khi nút nằm trong đúng
dialog có tiêu đề này; thao tác đó đóng gợi ý mà không bật thay đổi cài đặt account và
không hủy upload. Sau đó caption mới được focus/điền.

## Trình tự chạy

```text
resolve media
  -> photo file/folder valid? use up to 35 photos
  -> otherwise use valid fallback video
  -> otherwise reject before opening browser

login (cookie first, OTP fallback)
  -> /foryou
  -> wait ready + signed-in + feed, stable twice
  -> save refreshed cookies
  -> TikTok Studio Upload
  -> Photos tab when publishing photos
  -> native Windows file chooser
  -> wait editor + enabled Post
  -> fill caption
  -> locator.click(Post)
  -> require success text or Studio content redirect
```

## Locator tối thiểu

- Home readiness: một nhóm nav đăng nhập, một nhóm feed/main và nút login.
- Photo mode: `get_by_role("tab", name=/^(Photos|Ảnh)$/)`.
- File input: `input[type=file]` có `accept` ảnh tương ứng.
- Caption: `.public-DraftEditor-content` hoặc contenteditable đầu tiên.
- Primary action: đăng ngay chỉ nhận `Post|Publish|Đăng`; `Schedule|Lên lịch` chỉ
  được thêm sau khi chế độ lịch thực sự được bật.
- Success: thông báo thành công cụ thể hoặc URL `/tiktokstudio/content`.

Không còn các thao tác cũ sau:

- `element.click()` bằng JavaScript để bấm Post.
- quét tất cả button rồi click nhiều popup;
- quét `*` và tự đổi quyền công khai/bình luận/Duet/Stitch;
- coi URL `/foryou` là bằng chứng đăng nhập;
- trả `true` khi không thấy xác nhận đăng bài.

## API và UI

- `POST /api/v1/tasks/bulk-upload-media`
- `POST /api/v1/tasks/schedule-upload-media`
- Route video cũ vẫn được giữ làm alias để tương thích.
- UI nhận `image_path` (file hoặc thư mục) và `video_path` dự phòng.
- Ảnh được sắp theo tên file và giới hạn tối đa 35 file.

## Ghi chú về invisible_playwright

Repo dùng đúng package Git của `feder-cr/invisible_playwright`, `humanize=True` và seed ổn định theo account. Các click quan trọng đi qua locator để driver có thể áp dụng đường chuột/click humanized. Điều này giảm các dấu hiệu automation phổ biến nhưng không thể bảo đảm TikTok không phát hiện; tài liệu upstream cũng lưu ý hành vi và chất lượng IP/proxy vẫn ảnh hưởng.

Nguồn:

- https://github.com/feder-cr/invisible_playwright
- https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/playwright-detected-as-bot.md
- https://developers.tiktok.com/docs/en/content-posting-api-reference-photo-post
- https://developers.tiktok.com/docs/en/content-sharing-guidelines
