# KTP-el KAMU HILANG? — motion infographic

Video vertikal 9:16 (1080×1920, 30 fps, 15 detik) yang dibuat dari poster asli
Kapanewon Ngaglik (`poster.jpg`). Poster ini dijadikan master frame.

- `ktp-el-hilang-9x16.mp4` — video final untuk Reels, TikTok, Shorts, dan Status WA.
  Ada trek audio senyap, jadi musik bisa ditambahkan langsung di aplikasi.
- `render.py` — skrip render yang bisa dijalankan ulang (`pip install numpy opencv-python-headless imageio-ffmpeg`,
  lalu `python render.py`; `--preview` hanya menyimpan beberapa frame PNG).

Semua teks, logo, karakter, dan ikon diambil langsung dari piksel poster, tanpa digambar
ulang atau diketik ulang. Gerakan dibuat dari kamera (push-in, pan, zoom-out),
warp kecil yang lembut (kepala, tangan, dokumen, clipboard, kedipan pada ikon kamera),
lapisan kelopak mata untuk kedipan, light sweep, glow, dan depth of field.

Alur: 0–3,4 dtk judul dan logo · 3,4–6 dtk Langkah 1 · 6–8,8 dtk Langkah 2 ·
8,8–11,6 dtk Langkah 3 · 11,6–15 dtk zoom-out, gerakan serempak, dan poster penuh.
