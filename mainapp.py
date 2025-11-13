import argparse
import csv
import glob
import math
import os
import random
import sys
import subprocess
from datetime import datetime, date, timedelta

import customtkinter as ctk
from PIL import Image, ImageTk, ImageFilter, ImageChops
import pygame

# Appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


def parse_target_date_arg(s):
    """
    Parse a loose date string into a datetime.date.

    Accepts:
    - "today"/"now", "yesterday", "tomorrow"
    - Relative days like "+3" or "-2"
    - Explicit formats: "MM/DD/YYYY", "MM/DD/YY", "YYYY-MM-DD"

    If parsing fails, returns today's date.
    """
    if not s:
        return None
    txt = str(s).strip().lower()
    today = date.today()

    if txt in ("today", "now"):
        return today
    if txt == "yesterday":
        return today - timedelta(days=1)
    if txt == "tomorrow":
        return today + timedelta(days=1)

    if (txt.startswith("+") or txt.startswith("-")) and txt[1:].isdigit():
        return today + timedelta(days=int(txt))

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except Exception:
            pass
    print(f"Could not parse date: {s}. Falling back to today.")
    return today


class MoonApp(ctk.CTk):
    """
    Lunie application window.

    This CustomTkinter app:
    - Loads moon and face images (open/closed eyes).
    - Reads daily moon phase data from a CSV and computes a realistic phase shading mask.
    - Applies shading to the moon (and optionally to the face layer).
    - Animates subtle motion in response to mouse movement and adds a blink animation.
    - Optionally plays background music if a supported audio file exists.

    Parameters:
    - csv_path: Path to the CSV data with moon phase rows.
    - target_date: A datetime.date (or None to auto-pick today or nearest row).
    - hemisphere: "north" or "south" (affects which side is lit).
    - play_music: If True, attempt to play background music on loop.
    - shade_face: If True, also apply phase shading to the face PNG (if it includes a disc).
    - mask_softness: Gaussian blur in pixels (after oversampling) for the terminator softness.
    - mask_oversample: Oversampling factor for generating the phase mask (helps thin crescents).
    """

    def __init__(self, csv_path="moongiant_moon_daily.csv",
                 target_date=None,
                 hemisphere="north",
                 play_music=True,
                 shade_face=False,
                 mask_softness=0.9,
                 mask_oversample=2):
        super().__init__()

        # Configure window
        self.title("Lunie")
        # Use a .ico on Windows for a stable title-bar/taskbar icon
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            ico_path = os.path.join(base_dir, "icon.ico")
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
        except Exception as e:
            print(f"Icon set error: {e}")

        self.resizable(False, False)
        self.configure(fg_color="#0a1128")

        # Window geometry
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.window_width = screen_width // 4
        self.window_height = int(screen_height * 0.65)
        right_x = screen_width - self.window_width - 10
        top_y = 10
        self.geometry(f"{self.window_width}x{self.window_height}+{right_x}+{top_y}")
        self.update()

        # Canvas (main drawing surface)
        self.canvas = ctk.CTkCanvas(self, bg="#0a1128", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.update()
        self.canvas_width = self.canvas.winfo_width()
        self.canvas_height = self.canvas.winfo_height()

        # Background gradient
        self.create_gradient_background()

        # Store image refs
        self.moon_photo = None
        self.moon_face_photo = None
        self.moon_face_closed_photo = None

        # PIL images (resized)
        self.moon_pil = None
        self.moon_pil_original = None
        self.moon_face_pil = None
        self.moon_face_pil_original = None
        self.moon_face_closed_pil = None
        self.moon_face_closed_pil_original = None

        # Canvas IDs
        self.moon_image_id = None
        self.moon_face_image_id = None

        # Blinking
        self.is_blinking = False
        self.blink_timer = None

        # Info text
        self.info_text_id = None

        # Position (upper third)
        self.center_x = self.canvas_width / 2
        self.center_y = (self.canvas_height / 3)
        self.current_face_x = self.center_x
        self.current_face_y = self.center_y
        self.current_moon_x = self.center_x
        self.current_moon_y = self.center_y

        # Movement (parallax-style)
        self.max_offset_face = 15
        self.max_offset_moon = 5

        # Options
        self.hemisphere = hemisphere
        self.shade_face = shade_face
        self.mask_softness = mask_softness
        self.mask_oversample = max(1, int(mask_oversample))

        # Phase data
        self.phase_data = None
        self.current_date = None

        # Dark-side texture (new-moon)
        self.new_moon_pil_base = None

        # Measured disc geometry (from alpha)
        self.disc_cx = None
        self.disc_cy = None
        self.disc_R = None

        # Music
        if play_music:
            self.init_music()

        # Load data
        self.load_moon_data(csv_path, target_date=target_date)

        # Load images
        self.load_both_moons()

        # Measure actual moon disc in the base image alpha
        self.measure_moon_disc()

        # Load 'new-moon' texture
        self.load_new_moon_texture()

        # Apply shading
        self.apply_phase_shading()

        # Info text
        self.draw_phase_info_text()

        # Events
        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.canvas.bind("<Leave>", self.on_mouse_leave)

        # Blink
        self.schedule_next_blink()

    def create_gradient_background(self):
        """
        Paint a simple vertical gradient on the canvas to form a night-sky backdrop.
        Draws multiple horizontal rectangles with colors interpolated from dark to slightly lighter blue.
        """
        steps = 100
        width = self.canvas_width
        height = self.canvas_height
        r1, g1, b1 = 10, 10, 35
        r2, g2, b2 = 15, 30, 60
        for i in range(steps):
            ratio = i / steps
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            color = f'#{r:02x}{g:02x}{b:02x}'
            y1 = (height / steps) * i
            y2 = (height / steps) * (i + 1)
            self.canvas.create_rectangle(0, y1, width, y2, fill=color, outline=color)

    def init_music(self):
        """
        Initialize pygame.mixer and attempt to play a background track on loop.
        Looks for 'break_in_roblox_night_theme' in .mp3/.wav/.ogg in the script folder.
        If not found or audio init fails, it prints a message and continues.
        """
        try:
            pygame.mixer.init()
            music_files = [
                "break_in_roblox_night_theme.mp3",
                "break_in_roblox_night_theme.wav",
                "break_in_roblox_night_theme.ogg"
            ]
            music_path = None
            for m in music_files:
                if os.path.exists(m):
                    music_path = m
                    break
            if music_path:
                pygame.mixer.music.load(music_path)
                pygame.mixer.music.play(-1)
                pygame.mixer.music.set_volume(0.5)
            else:
                print("Music not found", music_files)
        except Exception as e:
            print(f"Music init error: {e}")

    def load_both_moons(self):
        """
        Load and place the base moon image and the moon-face overlay (open eyes) onto the canvas.
        Also loads the closed-eyes image for blinking (not placed immediately).
        """
        self.current_face_x = self.center_x
        self.current_face_y = self.center_y
        self.current_moon_x = self.center_x
        self.current_moon_y = self.center_y

        # Base
        self.moon_image_id = self.load_and_place_image("moon.png", self.center_x, self.center_y, is_face=False)

        # Face (open)
        self.moon_face_image_id = self.load_and_place_image("moon-face.png", self.center_x, self.center_y, is_face=True)

        # Face (closed)
        self.load_closed_eyes_image("moon-face-closed.png")

        # Ensure face above base
        if self.moon_face_image_id and self.moon_image_id:
            self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)

    def load_closed_eyes_image(self, image_path):
        """
        Load the closed-eyes face PNG for blink animation, resize it (keeping alpha), and cache it.

        Parameters:
        - image_path: Path to the closed-eyes PNG.
        """
        try:
            if not os.path.exists(image_path):
                print(f"Closed eyes '{image_path}' not found - blinking disabled")
                return
            img = Image.open(image_path)
            if img.mode not in ("RGBA", "RGB"):
                img = img.convert("RGBA")

            orig_w, orig_h = img.size
            max_w = self.canvas_width - 40 if self.canvas_width > 0 else self.window_width - 40
            max_h = self.canvas_height - 40 if self.canvas_height > 0 else self.window_height - 40
            max_size = min(max_w, max_h, 400)
            scale = min(max_size / orig_w, max_size / orig_h)
            if scale < 1:
                img = img.resize((int(orig_w * scale), int(orig_h * scale)), Image.Resampling.LANCZOS)

            self.moon_face_closed_pil_original = img.copy()
            self.moon_face_closed_pil = img
            self.moon_face_closed_photo = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Closed eyes load error: {e}")

    def load_and_place_image(self, image_path, x, y, is_face=False):
        """
        Load an image (moon or face), optionally resize to fit the canvas, and place it on the canvas.

        Parameters:
        - image_path: Path to the image to load.
        - x, y: Center coordinates to place the image on the canvas.
        - is_face: If True, this is the face overlay (stored separately); otherwise base moon.

        Returns:
        - The canvas image item ID, or None if loading failed.
        """
        try:
            if not os.path.exists(image_path):
                print(f"Image '{image_path}' not found!")
                return None
            img = Image.open(image_path)
            if img.mode not in ("RGBA", "RGB"):
                img = img.convert("RGBA")

            orig_w, orig_h = img.size
            max_w = self.canvas_width - 40 if self.canvas_width > 0 else self.window_width - 40
            max_h = self.canvas_height - 40 if self.canvas_height > 0 else self.window_height - 40
            max_size = min(max_w, max_h, 400)
            scale = min(max_size / orig_w, max_size / orig_h)
            if scale < 1:
                img = img.resize((int(orig_w * scale), int(orig_h * scale)), Image.Resampling.LANCZOS)

            if is_face:
                self.moon_face_pil_original = img.copy()
                self.moon_face_pil = img
                photo = ImageTk.PhotoImage(img)
                self.moon_face_photo = photo
            else:
                self.moon_pil_original = img.copy()
                self.moon_pil = img
                photo = ImageTk.PhotoImage(img)
                self.moon_photo = photo

            image_id = self.canvas.create_image(x, y, image=photo, anchor="center")
            return image_id
        except Exception as e:
            print(f"Error loading image {image_path}: {e}")
            return None

    def measure_moon_disc(self):
        """
        Analyze the base moon image's alpha channel to estimate the circular disc:
        center (cx, cy) and radius (R). Slightly expands R to cover AA edges and avoid halos.
        Falls back to center of the image if alpha bbox isn't found.
        """
        if self.moon_pil_original is None:
            return
        img = self.moon_pil_original.convert("RGBA")
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        w, h = img.size
        if not bbox:
            # fallback to full image center
            self.disc_cx = w / 2.0
            self.disc_cy = h / 2.0
            self.disc_R = min(w, h) / 2.0 - 1.0
            print(f"[disc] fallback center=({self.disc_cx:.1f},{self.disc_cy:.1f}) R={self.disc_R:.1f}")
            return

        l, t, r, b = bbox
        bw = r - l
        bh = b - t
        cx = l + bw / 2.0
        cy = t + bh / 2.0
        R = min(bw, bh) / 2.0

        # EXPAND radius slightly to cover anti-aliased edges and avoid halo
        R = R + 2.0

        self.disc_cx = cx
        self.disc_cy = cy
        self.disc_R = R
        print(f"[disc] center=({cx:.2f},{cy:.2f}) R={R:.2f} (bbox {bw}x{bh} in {w}x{h})")

    def schedule_next_blink(self):
        """
        Schedule the next blink at a random interval (3-7 seconds).
        Requires that the closed-eyes image was loaded successfully.
        """
        if self.moon_face_closed_photo is None:
            return
        self.blink_timer = self.after(random.randint(3000, 7000), self.start_blink)

    def start_blink(self):
        """
        Begin a blink by swapping the face image to the closed-eyes version briefly,
        then schedule end_blink() shortly after (150-200 ms).
        """
        if self.is_blinking or self.moon_face_image_id is None:
            return
        self.is_blinking = True
        self.canvas.itemconfig(self.moon_face_image_id, image=self.moon_face_closed_photo)
        if self.moon_image_id is not None:
            self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
        self.after(random.randint(150, 200), self.end_blink)

    def end_blink(self):
        """
        End a blink by restoring the face image back to the open-eyes version,
        then schedule another blink in the future.
        """
        if self.moon_face_image_id is None:
            return
        self.canvas.itemconfig(self.moon_face_image_id, image=self.moon_face_photo)
        self.is_blinking = False
        if self.moon_image_id is not None:
            self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
        self.schedule_next_blink()

    def on_mouse_move(self, event):
        """
        Handle mouse movement over the canvas. Applies a subtle parallax:
        the face and moon layers shift slightly toward the cursor, limited by max offsets.
        """
        if self.moon_face_image_id is None or self.moon_image_id is None:
            return
        if getattr(self, "animation_id", None) is not None:
            self.after_cancel(self.animation_id)
            self.animation_id = None

        dx = event.x - self.center_x
        dy = event.y - self.center_y
        distance = math.sqrt(dx * dx + dy * dy)
        if distance == 0:
            offset_face_x = offset_face_y = offset_moon_x = offset_moon_y = 0
        else:
            factor = min(distance / (self.canvas_width / 2), 1.0)
            offset_face_x = (dx / distance) * self.max_offset_face * factor
            offset_face_y = (dy / distance) * self.max_offset_face * factor
            offset_moon_x = (dx / distance) * self.max_offset_moon * factor
            offset_moon_y = (dy / distance) * self.max_offset_moon * factor

        self.current_face_x = self.center_x + offset_face_x
        self.current_face_y = self.center_y + offset_face_y
        self.canvas.coords(self.moon_face_image_id, self.current_face_x, self.current_face_y)

        self.current_moon_x = self.center_x + offset_moon_x
        self.current_moon_y = self.center_y + offset_moon_y
        self.canvas.coords(self.moon_image_id, self.current_moon_x, self.current_moon_y)

        self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
        if self.info_text_id is not None:
            self.canvas.tag_raise(self.info_text_id)

    def on_mouse_leave(self, event):
        """
        When the mouse leaves the canvas, smoothly animate both layers back to center.
        """
        self.animate_to_center()

    def animate_to_center(self):
        """
        Easing-based animation that moves the face and moon images back to center over time.
        Uses a small 'ease' factor and reschedules itself (~60 FPS) until centered.
        """
        if self.moon_face_image_id is None or self.moon_image_id is None:
            return
        dx_face = self.center_x - self.current_face_x
        dy_face = self.center_y - self.current_face_y
        dx_moon = self.center_x - self.current_moon_x
        dy_moon = self.center_y - self.current_moon_y
        if abs(dx_face) < 0.5 and abs(dy_face) < 0.5 and abs(dx_moon) < 0.5 and abs(dy_moon) < 0.5:
            self.current_face_x = self.center_x
            self.current_face_y = self.center_y
            self.current_moon_x = self.center_x
            self.current_moon_y = self.center_y
            self.canvas.coords(self.moon_face_image_id, self.current_face_x, self.current_face_y)
            self.canvas.coords(self.moon_image_id, self.current_moon_x, self.current_moon_y)
            self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
            if self.info_text_id is not None:
                self.canvas.tag_raise(self.info_text_id)
            self.animation_id = None
            return

        ease = 0.15
        self.current_face_x += dx_face * ease
        self.current_face_y += dy_face * ease
        self.canvas.coords(self.moon_face_image_id, self.current_face_x, self.current_face_y)
        self.current_moon_x += dx_moon * ease
        self.current_moon_y += dy_moon * ease
        self.canvas.coords(self.moon_image_id, self.current_moon_x, self.current_moon_y)
        self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
        if self.info_text_id is not None:
            self.canvas.tag_raise(self.info_text_id)
        self.animation_id = self.after(16, self.animate_to_center)

    # ---------- Phase shading + CSV ----------

    def load_moon_data(self, csv_path="moongiant_moon_daily.csv", target_date=None):
        """
        Load and parse the moon phase CSV into memory, pick the best row for target_date,
        and store it in self.phase_data.

        Row fields expected:
        - date, phase, illumination_pct, moon_age_days, moon_angle_deg, moon_distance_km,
          sun_angle_deg, sun_distance_km

        Target selection:
        - Exact match on date if available
        - Else most recent past row
        - Else earliest row in the file
        """
        self.phase_data = None
        self.current_date = None
        if not os.path.exists(csv_path):
            print(f"Moon CSV not found: {csv_path}")
            return

        rows = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    row = {
                        "date": r.get("date", "").strip(),
                        "phase": r.get("phase", "").strip(),
                        "illumination_pct": r.get("illumination_pct", "").strip(),
                        "moon_age_days": r.get("moon_age_days", "").strip(),
                        "moon_angle_deg": r.get("moon_angle_deg", "").strip(),
                        "moon_distance_km": r.get("moon_distance_km", "").strip(),
                        "sun_angle_deg": r.get("sun_angle_deg", "").strip(),
                        "sun_distance_km": r.get("sun_distance_km", "").strip(),
                    }
                    try:
                        d = datetime.strptime(row["date"], "%m/%d/%Y").date()
                    except ValueError:
                        try:
                            d = datetime.strptime(row["date"], "%m/%d/%y").date()
                        except Exception:
                            continue
                    row["_date"] = d

                    def to_float(v, default=None):
                        try:
                            return float(v)
                        except:
                            return default

                    row["_illum"] = to_float(row["illumination_pct"], None)
                    row["_age"] = to_float(row["moon_age_days"], None)
                    row["_moon_angle"] = to_float(row["moon_angle_deg"], None)
                    row["_sun_angle"] = to_float(row["sun_angle_deg"], None)
                    rows.append(row)
        except Exception as e:
            print(f"CSV read error: {e}")
            return

        if not rows:
            print("No rows parsed from CSV.")
            return

        tgt = target_date if isinstance(target_date, date) else (
            parse_target_date_arg(target_date) if target_date else date.today())

        exact = [r for r in rows if r["_date"] == tgt]
        if exact:
            chosen = exact[0]
        else:
            past = [r for r in rows if r["_date"] <= tgt]
            if past:
                chosen = sorted(past, key=lambda r: r["_date"])[-1]
            else:
                future = sorted(rows, key=lambda r: r["_date"])
                chosen = future[0]

        self.phase_data = chosen
        self.current_date = chosen["_date"]
        print(f"Phase {chosen['phase']} • {chosen['_illum']}% • {chosen['_date']}")

    def is_waxing_from_phase(self, phase_name: str) -> bool:
        """
        Infer waxing/waning from the phase name text.

        Returns:
        - True for waxing / first quarter / new moon (default True for unknown/full)
        - False for waning / last quarter
        """
        if not phase_name:
            return True
        p = phase_name.lower()
        if "waxing" in p or "first quarter" in p or "new moon" in p:
            return True
        if "waning" in p or "last quarter" in p:
            return False
        return True  # Full or unknown

    def draw_phase_info_text(self):
        """
        Draw or update the info text line at the bottom:
        "MM/DD/YYYY • Phase Name • XX% • Age Y.Y d"
        """
        if not self.phase_data:
            return
        illum = self.phase_data.get("_illum")
        age = self.phase_data.get("_age")
        phase = self.phase_data.get("phase", "")
        d = self.phase_data.get("_date")
        illum_txt = f"{int(illum)}%" if illum is not None else "?"
        age_txt = f"{age:.1f}" if age is not None else "?"
        date_txt = d.strftime("%m/%d/%Y") if isinstance(d, date) else str(self.phase_data.get("date", ""))
        txt = f"{date_txt} • {phase} • {illum_txt} • Age {age_txt} d"
        y = self.canvas_height - 24
        if self.info_text_id is None:
            self.info_text_id = self.canvas.create_text(self.canvas_width / 2, y, text=txt, fill="#e8eefc",
                                                        font=("Segoe UI", 14, "bold"))
        else:
            self.canvas.itemconfig(self.info_text_id, text=txt)
        if self.moon_face_image_id is not None:
            self.canvas.tag_raise(self.info_text_id, self.moon_face_image_id)

    def apply_phase_shading(self):
        """
        Compute and apply a physically-inspired phase mask to the moon image:
        - Uses illumination percentage to compute the sun direction (via cos relationship).
        - Generates a high-quality binary mask (lit vs dark), feathered by Gaussian blur.
        - Composites the lit and dark sides (dark side uses new-moon texture or black).
        - Restores original alpha to maintain crisp edges.

        Optionally shades the face layer as well if self.shade_face is True.
        """
        if not self.phase_data or self.moon_pil_original is None:
            return

        illum = self.phase_data.get("_illum", None)
        phase_name = self.phase_data.get("phase", "")
        if illum is None:
            print("No illumination percentage — skipping shading.")
            return

        k = max(0.0, min(1.0, float(illum) / 100.0))
        waxing = self.is_waxing_from_phase(phase_name)
        if self.hemisphere.lower().startswith("south"):
            waxing = not waxing

        # Base moon shading
        base = self.moon_pil_original.convert("RGBA")
        w, h = base.size
        cx = self.disc_cx if self.disc_cx is not None else w / 2.0
        cy = self.disc_cy if self.disc_cy is not None else h / 2.0
        R = self.disc_R if self.disc_R is not None else min(w, h) / 2.0

        # Get original alpha channel
        base_alpha = base.split()[3]

        mask_dark = self.generate_phase_alpha_mask(
            w, h, k, waxing,
            softness=self.mask_softness,
            cx=cx, cy=cy, R=R,
            oversample=self.mask_oversample
        )  # 255=dark, 0=lit

        # Constrain mask to original alpha channel to prevent shadow bleeding outside moon
        mask_dark = ImageChops.multiply(mask_dark, base_alpha)

        if self.new_moon_pil_base is not None and self.new_moon_pil_base.size == (w, h):
            dark_tex = self.new_moon_pil_base.copy().convert("RGBA")
        elif self.new_moon_pil_base is not None:
            dark_tex = self.new_moon_pil_base.resize((w, h), Image.Resampling.LANCZOS).convert("RGBA")
        else:
            dark_tex = Image.new("RGBA", (w, h), (0, 0, 0, 255))

        shaded = Image.composite(dark_tex, base, mask_dark)

        # Restore original alpha to preserve exact transparency
        shaded.putalpha(base_alpha)

        self.moon_pil = shaded.copy()
        self.moon_photo = ImageTk.PhotoImage(shaded)
        self.canvas.itemconfig(self.moon_image_id, image=self.moon_photo)

        # Optional: also shade face (if your face PNG includes a disc)
        if self.shade_face and self.moon_face_pil_original is not None:
            face_base = self.moon_face_pil_original.convert("RGBA")
            wf, hf = face_base.size
            face_alpha = face_base.split()[3]

            cx_f, cy_f, R_f = self.measure_disc_inline(face_base)
            mask_f = self.generate_phase_alpha_mask(
                wf, hf, k, waxing,
                softness=self.mask_softness,
                cx=cx_f, cy=cy_f, R=R_f,
                oversample=self.mask_oversample
            )

            # Constrain to face alpha
            mask_f = ImageChops.multiply(mask_f, face_alpha)

            face_dark = Image.new("RGBA", (wf, hf), (0, 0, 0, 255))
            face_open_shaded = Image.composite(face_dark, face_base, mask_f)
            face_open_shaded.putalpha(face_alpha)

            self.moon_face_pil = face_open_shaded
            self.moon_face_photo = ImageTk.PhotoImage(self.moon_face_pil)
            self.canvas.itemconfig(self.moon_face_image_id, image=self.moon_face_photo)

            if self.moon_face_closed_pil_original is not None:
                face_closed_base = self.moon_face_closed_pil_original.convert("RGBA")
                face_closed_alpha = face_closed_base.split()[3]
                mask_f_closed = ImageChops.multiply(mask_f, face_closed_alpha)
                face_closed_shaded = Image.composite(face_dark, face_closed_base, mask_f_closed)
                face_closed_shaded.putalpha(face_closed_alpha)
                self.moon_face_closed_pil = face_closed_shaded
                self.moon_face_closed_photo = ImageTk.PhotoImage(self.moon_face_closed_pil)

        if self.moon_face_image_id is not None:
            self.canvas.tag_raise(self.moon_face_image_id, self.moon_image_id)
        if self.info_text_id is not None:
            self.canvas.tag_raise(self.info_text_id)

    def generate_phase_alpha_mask(self, w, h, k, waxing=True, softness=0.9, cx=None, cy=None, R=None, oversample=2):
        """
        Generate a grayscale mask (mode 'L') where 255 is dark side and 0 is lit side.

        Math:
        - Let k = illuminated fraction in [0..1].
        - We use k = (1 + cos(a))/2 => cos(a) = 2k - 1 to get the angle 'a'.
        - Form a sun direction vector in the image plane (sx) and toward viewer (sz).
        - For each pixel within the circle, compute n · s (normal dot sun).
          If ndot <= 0 => dark side; else lit side.

        Parameters:
        - w, h: Image dimensions.
        - k: illumination fraction [0..1].
        - waxing: True if waxing (lit on the right in northern hemisphere).
        - softness: Gaussian blur radius (after oversample) to feather the terminator.
        - cx, cy, R: Circle center and radius (if None, use image center/min dimension).
        - oversample: Build the mask at a larger resolution to preserve thin crescents,
          then downsample with antialiasing.

        Returns:
        - PIL.Image in mode "L" with 255=dark and 0=lit.
        """
        # Edge cases
        if k <= 0.0:
            mask = Image.new("L", (w, h), 0)
            self._draw_circle_mask(mask, cx, cy, R, fill=255)
            return mask
        if k >= 1.0:
            return Image.new("L", (w, h), 0)

        # k = (1 + cos a)/2 => cos a = 2k - 1
        cos_a = max(-1.0, min(1.0, 2.0 * k - 1.0))
        a = math.acos(cos_a)
        sx = (math.sin(a) if waxing else -math.sin(a))
        sz = cos_a

        # Defaults if not provided
        if cx is None: cx = w / 2.0
        if cy is None: cy = h / 2.0
        if R is None: R = min(w, h) / 2.0

        os = max(1, int(oversample))
        W = w * os
        H = h * os
        CX = cx * os
        CY = cy * os
        R2 = R * os

        mask_hi = Image.new("L", (W, H), 0)
        px = mask_hi.load()

        # Be more lenient with the boundary to avoid halo
        boundary_tolerance = 1.15

        for j in range(H):
            y = (j + 0.5 - CY) / R2
            for i in range(W):
                x = (i + 0.5 - CX) / R2
                r2 = x * x + y * y
                if r2 > boundary_tolerance:
                    continue
                nz2 = 1.0 - r2
                if nz2 <= 0:
                    nz2 = 0.0001  # Small value to avoid math errors
                nz = math.sqrt(nz2)
                ndot = x * sx + nz * sz
                px[i, j] = 255 if ndot <= 0 else 0

        # Feather the terminator slightly, scaled by oversample
        if softness and softness > 0:
            mask_hi = mask_hi.filter(ImageFilter.GaussianBlur(softness * os))

        # Downsample back to image size with antialias
        if os > 1:
            mask = mask_hi.resize((w, h), Image.Resampling.LANCZOS)
        else:
            mask = mask_hi

        return mask

    def _draw_circle_mask(self, img_l_mask, cx, cy, R, fill=255):
        """
        Draw a filled circle into a grayscale mask.
        Used for the 0% illumination special case or utility rendering.
        """
        from PIL import ImageDraw
        w, h = img_l_mask.size
        r = max(1.0, R)
        bbox = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        draw = ImageDraw.Draw(img_l_mask)
        draw.ellipse(bbox, fill=fill)

    def measure_disc_inline(self, img):
        """
        Compute a disc center and radius (cx, cy, R) for an arbitrary RGBA image by
        inspecting its alpha channel bounding box.

        Slight radius expansion (+2px) helps cover anti-aliased edges.
        Returns (cx, cy, R).
        """
        im = img.convert("RGBA")
        w, h = im.size
        alpha = im.split()[-1]
        bbox = alpha.getbbox()
        if not bbox:
            return (w / 2.0, h / 2.0, min(w, h) / 2.0 - 1.0)
        l, t, r, b = bbox
        bw = r - l
        bh = b - t
        cx = l + bw / 2.0
        cy = t + bh / 2.0
        R = min(bw, bh) / 2.0 + 2.0  # Expand instead of shrink
        return (cx, cy, max(1.0, R))

    # ---------- new-moon texture loader ----------

    def load_new_moon_texture(self, base_name="new-moon"):
        """
        Attempt to load a 'new-moon' texture (used for the dark side of the moon).
        Tries multiple filename variants and common image extensions in the working directory.

        If found, resizes it to match the current moon image size and caches it.
        If not found, falls back to solid black.
        """
        candidates = []
        for name in [base_name, base_name.replace("-", "_"), base_name.replace("-", "")]:
            for ext in ["", ".png", ".webp", ".jpg", ".jpeg", ".bmp"]:
                p = name + ext
                if os.path.exists(p) and os.path.isfile(p):
                    candidates.append(p)
        if not candidates:
            found = glob.glob(f"{base_name}*")
            candidates = [p for p in found if os.path.isfile(p)]

        if not candidates:
            print("No 'new-moon' texture found. Using solid black for dark side.")
            return

        path = candidates[0]
        try:
            img = Image.open(path)
            if img.mode not in ("RGBA", "RGB"):
                img = img.convert("RGBA")
            if self.moon_pil_original is not None:
                self.new_moon_pil_base = img.resize(self.moon_pil_original.size, Image.Resampling.LANCZOS)
            print(f"Loaded new-moon texture: {path}")
        except Exception as e:
            print(f"Failed to load 'new-moon' texture: {e}")
            print("Falling back to solid black for dark side.")


# ---------- Windows: create a desktop shortcut on first run ----------
def _get_windows_desktop_dir():
    """
    Resolve the current user's Desktop directory on Windows using SHGetFolderPathW.
    Falls back to ~/Desktop if the shell call fails.
    Returns the absolute path as a string, or None on non-Windows.
    """
    if os.name != "nt":
        return None
    try:
        from ctypes import windll, wintypes, create_unicode_buffer
        CSIDL_DESKTOPDIRECTORY = 0x0010
        SHGFP_TYPE_CURRENT = 0
        buf = create_unicode_buffer(wintypes.MAX_PATH)
        if windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, SHGFP_TYPE_CURRENT, buf) == 0:
            return buf.value
    except Exception:
        pass
    # Fallback
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _ensure_windows_desktop_shortcut():
    """
    Create a Windows shortcut 'Lunie.lnk' on the Desktop if it doesn't exist.

    Behavior:
    - Uses pythonw.exe (no console) if available; otherwise uses the current Python interpreter.
    - Points the shortcut at this script (quoted path as argument).
    - Uses icon.ico from the script's directory if present.
    - Implemented via a small PowerShell command that creates the shortcut
      using the WScript.Shell COM object. Does nothing on non-Windows.
    """
    if os.name != "nt":
        return
    try:
        desktop = _get_windows_desktop_dir()
        if not desktop or not os.path.isdir(desktop):
            return

        shortcut_path = os.path.join(desktop, "Lunie.lnk")
        if os.path.exists(shortcut_path):
            return  # already created

        script_path = os.path.abspath(__file__)
        script_dir = os.path.dirname(script_path)

        # Prefer pythonw.exe (no console) if present
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        target = pyw if os.path.exists(pyw) else exe

        # Use icon.ico from script folder if available
        icon_path = os.path.join(script_dir, "icon.ico")
        use_icon = os.path.exists(icon_path)

        def ps_quote(p):
            # PowerShell single-quoted literal; double single quotes inside
            return p.replace("'", "''")

        # Arguments: the script path wrapped in double quotes
        args_str = f'"{script_path}"'

        ps = []
        ps.append("$WshShell = New-Object -ComObject WScript.Shell")
        ps.append(f"$Shortcut = $WshShell.CreateShortcut('{ps_quote(shortcut_path)}')")
        ps.append(f"$Shortcut.TargetPath = '{ps_quote(target)}'")
        ps.append(f"$Shortcut.Arguments = '{ps_quote(args_str)}'")
        ps.append(f"$Shortcut.WorkingDirectory = '{ps_quote(script_dir)}'")
        if use_icon:
            ps.append(f"$Shortcut.IconLocation = '{ps_quote(icon_path)},0'")
        ps.append("$Shortcut.Save()")
        cmd = "; ".join(ps)

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Shortcut creation error: {e}")


if __name__ == "__main__":
    # On first run (Windows), create a desktop shortcut that launches this script
    _ensure_windows_desktop_shortcut()

    parser = argparse.ArgumentParser(description="Moon Tracker — phase shading from CSV (no overlay).")
    parser.add_argument("--csv", default="moongiant_moon_daily.csv", help="Path to CSV dataset.")
    parser.add_argument("--date", "-d", default=None,
                        help="Target date (MM/DD/YYYY, YYYY-MM-DD, today, yesterday, tomorrow, +N, -N).")
    parser.add_argument("--hemisphere", choices=["north", "south"], default="north",
                        help="Flip lit side for southern hemisphere.")
    parser.add_argument("--no-music", action="store_true", help="Disable background music.")
    parser.add_argument("--shade-face", action="store_true",
                        help="Also apply phase shading to the face layer (use if face PNG includes a disc).")
    parser.add_argument("--softness", type=float, default=0.9,
                        help="Gaussian blur (px) for the terminator (after oversampling).")
    parser.add_argument("--oversample", type=int, default=2,
                        help="Mask oversampling factor (1=off, 2=default).")
    args = parser.parse_args()

    target = parse_target_date_arg(args.date) if args.date else None

    app = MoonApp(
        csv_path=args.csv,
        target_date=None,
        hemisphere=args.hemisphere,
        play_music=(not args.no_music),
        shade_face=args.shade_face,
        mask_softness=args.softness,
        mask_oversample=args.oversample
    )
    app.mainloop()