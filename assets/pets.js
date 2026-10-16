/* Kaiana's pets: the little sprites that walk, hop and fly around the site.
   Shared by the public site (index.html) and the Sprite Studio in the lounge.

   A sprite is either an animated GIF or a PNG "sheet": `frames` equal-width
   pictures side by side, played left to right at `fps`. */
(function () {
  'use strict';

  const SIZES = { s: 40, m: 56, l: 80, xl: 112 };
  const SPEEDS = { slow: 0.55, normal: 1, fast: 1.8 };
  const BASE_SPEED = { walk: 42, hop: 70, fly: 70, float: 26, follow: 120 }; // px per second
  const GROUNDED = { walk: true, hop: true };

  const MOTIONS = {
    walk:   { label: 'Walk',   hint: 'strolls along the bottom, stops for little breaks' },
    hop:    { label: 'Hop',    hint: 'bounces along the bottom' },
    fly:    { label: 'Fly',    hint: 'flutters around anywhere' },
    float:  { label: 'Float',  hint: 'drifts slowly, like a balloon' },
    follow: { label: 'Follow', hint: 'chases the mouse (or your finger)' },
  };

  // Example pets drawn as text: one character per pixel, '.' is see-through.
  const TEMPLATES = {
    cat: {
      label: 'Cat', motion: 'walk', fps: 5,
      colors: { k: '#3a1030', p: '#ff9fd2', w: '#fff0f7', e: '#2a0a20', n: '#ff2d87' },
      frames: [
        ['................', '................', '................', '.........k...k..',
         '.k......kpk.kpk.', 'kpk.....kpppppk.', 'kpk.....kpepepk.', 'kpkkkkkkkppnppk.',
         '.kppppppppwwwpk.', '.kpppppppppkkk..', '.kppwwwwppppk...', '.kpppppppppk....',
         '..kkkkkkkkk.....', '..kpk....kpk....', '..kkk....kkk....', '................'],
        ['................', '................', '................', '.........k...k..',
         'k.......kpk.kpk.', 'kpk.....kpppppk.', 'kpk.....kpepepk.', 'kpkkkkkkkppnppk.',
         '.kppppppppwwwpk.', '.kpppppppppkkk..', '.kppwwwwppppk...', '.kpppppppppk....',
         '..kkkkkkkkk.....', '...kpk..kpk.....', '...kkk..kkk.....', '................'],
      ],
    },
    bat: {
      label: 'Bat', motion: 'fly', fps: 6,
      colors: { k: '#2a0a20', p: '#6b2c6b', v: '#ff5fa8', e: '#ffd84a', n: '#ff9fd2' },
      frames: [
        ['................', '................', 'k..............k', 'kk...k....k...kk',
         'kvk.kpk..kpk.kvk', 'kvvkkppppppkkvvk', 'kvvvkpeppepkvvvk', '.kvvkppnnppkvvk.',
         '..kvkppppppkvk..', '...kkppppppkk...', '....kppppppk....', '.....kkkkkk.....',
         '......k..k......', '................', '................', '................'],
        ['................', '................', '................', '.....k....k.....',
         '....kpk..kpk....', '....kppppppk....', '....kpeppepk....', 'kvvvkppnnppkvvvk',
         'kvvvkppppppkvvvk', '.kvvkppppppkvvk.', '..kvkppppppkvk..', '...kkkkkkkkkk...',
         '......k..k......', '................', '................', '................'],
      ],
    },
    slime: {
      label: 'Slime', motion: 'hop', fps: 4,
      colors: { k: '#3a1030', p: '#ff8fcf', w: '#ffffff', e: '#2a0a20', n: '#ff2d87' },
      frames: [
        ['................', '................', '................', '................',
         '................', '................', '......kkkk......', '....kkppppkk....',
         '...kppppppwpk...', '..kppppppppwpk..', '..kppeppppeppk..', '.kpppppnnpppppk.',
         '.kppppppppppppk.', '.kppppppppppppk.', '..kkkkkkkkkkkk..', '................'],
        ['................', '................', '................', '................',
         '................', '................', '................', '................',
         '.....kkkkkk.....', '...kkppppppwkk..', '..kppppppppppwk.', '.kppeppppppeppk.',
         'kppppppnnppppppk', 'kppppppppppppppk', '.kkkkkkkkkkkkkk.', '................'],
      ],
    },
  };

  /* ── pixel helpers ─────────────────────────────────────────── */

  // Template -> { w, h, frames: [ [color|null, ...] ] }
  function templatePixels(name) {
    const tpl = TEMPLATES[name];
    const h = tpl.frames[0].length, w = tpl.frames[0][0].length;
    const frames = tpl.frames.map(rows => {
      const px = new Array(w * h).fill(null);
      rows.forEach((row, y) => {
        for (let x = 0; x < w; x++) px[y * w + x] = tpl.colors[row[x]] || null;
      });
      return px;
    });
    return { w, h, frames };
  }

  // Frames of pixels -> canvas with the frames side by side (a sprite sheet).
  function pixelsToSheet(frames, w, h) {
    const c = document.createElement('canvas');
    c.width = w * frames.length;
    c.height = h;
    const ctx = c.getContext('2d');
    frames.forEach((px, f) => {
      for (let i = 0; i < px.length; i++) {
        if (!px[i]) continue;
        ctx.fillStyle = px[i];
        ctx.fillRect(f * w + (i % w), Math.floor(i / w), 1, 1);
      }
    });
    return c;
  }

  function templateSprite(name) {
    const tpl = TEMPLATES[name];
    const { w, h, frames } = templatePixels(name);
    return {
      name, url: pixelsToSheet(frames, w, h).toDataURL('image/png'), kind: 'sheet',
      frames: frames.length, fps: tpl.fps, motion: tpl.motion, speed: 'normal', size: 'm',
    };
  }

  async function fetchSprites(apiBase) {
    try {
      const r = await fetch((apiBase || '') + '/api/sprites/list');
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      const items = (data.items || []).map(i => ({ ...i, url: i.url.startsWith('http') || i.url.startsWith('data:') ? i.url : (apiBase || '') + i.url }));
      return { items, storage: data.storage };
    } catch (e) {
      return { items: [], storage: 'offline' };
    }
  }

  // Natural size of one frame; cached per url.
  const infoCache = new Map();
  function spriteInfo(sprite) {
    const key = sprite.url + '|' + sprite.frames;
    if (!infoCache.has(key)) {
      infoCache.set(key, new Promise(resolve => {
        const img = new Image();
        img.onload = () => {
          const frames = sprite.kind === 'gif' ? 1 : Math.max(1, sprite.frames || 1);
          const fw = img.naturalWidth / frames, fh = img.naturalHeight;
          resolve({ ok: true, fw, fh, aspect: fw / fh || 1 });
        };
        img.onerror = () => resolve({ ok: false, fw: 1, fh: 1, aspect: 1 });
        img.src = sprite.url;
      }));
    }
    return infoCache.get(key);
  }
  function forget(url) {
    for (const key of Array.from(infoCache.keys())) if (key.startsWith(url + '|')) infoCache.delete(key);
  }

  /* ── styles (injected once) ────────────────────────────────── */

  function injectStyles() {
    if (document.getElementById('pets-css')) return;
    const s = document.createElement('style');
    s.id = 'pets-css';
    s.textContent = `
.pets-layer{position:absolute;inset:0;overflow:hidden;pointer-events:none}
.pets-layer.pets-fixed{position:fixed;z-index:60}
.pet{position:absolute;left:0;top:0;will-change:transform;pointer-events:auto;cursor:pointer;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.pets-layer.passive .pet{pointer-events:none}
.pet-bounce{width:100%;height:100%;transform-origin:50% 100%}
.pet-art{width:100%;height:100%;background-repeat:no-repeat;image-rendering:pixelated;image-rendering:crisp-edges}
.pet-art img{width:100%;height:100%;display:block;image-rendering:pixelated;pointer-events:none;-webkit-user-drag:none;user-select:none}
.pet.flip .pet-art{transform:scaleX(-1)}
.pet.walk::after{content:'';position:absolute;left:22%;right:22%;bottom:-2px;height:4px;border-radius:50%;background:rgba(20,0,15,.28);z-index:-1}
.pet.squish .pet-bounce{animation:pet-squish .38s ease}
@keyframes pet-squish{30%{transform:scale(1.18,.78)}65%{transform:scale(.9,1.12)}}
.pet-fx{position:absolute;pointer-events:none;font:700 14px/1 system-ui,sans-serif;color:#ff2d87;text-shadow:0 1px 0 #fff;animation:pet-fx .9s ease-out forwards}
@keyframes pet-fx{from{opacity:1;transform:translate(-50%,0) scale(.6)}to{opacity:0;transform:translate(calc(-50% + var(--dx,0px)),var(--dy,-48px)) scale(1.15)}}
.pet-thumb{display:block;background-repeat:no-repeat;image-rendering:pixelated;margin:auto}
.pet-thumb.playing{animation:pet-thumb var(--dur,1s) steps(var(--n,1)) infinite}
@keyframes pet-thumb{to{background-position:var(--end,0) 0}}
@media (prefers-reduced-motion:reduce){.pet-thumb.playing{animation-duration:calc(var(--dur,1s)*2)}}
`;
    document.head.appendChild(s);
  }

  /* ── thumbnails (animated with CSS, no JS loop) ────────────── */

  function thumb(sprite, box) {
    injectStyles();
    box = box || 48;
    const wrap = document.createElement('span');
    wrap.style.cssText = `display:inline-flex;width:${box}px;height:${box}px;align-items:center;justify-content:center`;
    spriteInfo(sprite).then(info => {
      const scale = Math.min(box / info.fw, box / info.fh);
      const dw = Math.round(info.fw * scale), dh = Math.round(info.fh * scale);
      if (sprite.kind === 'gif') {
        const img = document.createElement('img');
        img.src = sprite.url; img.alt = ''; img.width = dw; img.height = dh;
        img.style.imageRendering = 'pixelated';
        wrap.appendChild(img);
        return;
      }
      const n = Math.max(1, sprite.frames || 1);
      const el = document.createElement('span');
      el.className = 'pet-thumb' + (n > 1 ? ' playing' : '');
      el.style.cssText = `width:${dw}px;height:${dh}px;background-image:url("${sprite.url}");background-size:${dw * n}px ${dh}px;` +
        `--n:${n};--end:-${dw * n}px;--dur:${(n / (sprite.fps || 6)).toFixed(3)}s`;
      wrap.appendChild(el);
    });
    return wrap;
  }

  /* ── a single pet ──────────────────────────────────────────── */

  const rand = (a, b) => a + Math.random() * (b - a);

  class Pet {
    constructor(stage, sprite, info, at) {
      this.stage = stage;
      this.sprite = sprite;
      this.motion = MOTIONS[sprite.motion] ? sprite.motion : 'walk';
      this.h = SIZES[sprite.size] || SIZES.m;
      this.w = Math.max(8, Math.round(this.h * info.aspect));
      this.frames = sprite.kind === 'gif' ? 1 : Math.max(1, sprite.frames || 1);
      this.fps = sprite.fps || 6;
      this.speed = BASE_SPEED[this.motion] * (SPEEDS[sprite.speed] || 1);
      this.dir = Math.random() < 0.5 ? 1 : -1;
      this.vx = 0; this.vy = 0;
      this.state = 'move'; this.timer = rand(1.5, 4);
      this.onGround = true;
      this.phase = Math.random() * 1000;
      this.lastFrame = -1;
      this.target = null;

      const b = stage.bounds();
      this.x = at && at.x != null ? at.x - this.w / 2 : rand(0, Math.max(0, b.w - this.w));
      this.y = GROUNDED[this.motion] ? this.groundY(b) : (at && at.y != null ? at.y - this.h / 2 : rand(0, Math.max(0, b.h * 0.6 - this.h)));
      this.build();
    }

    build() {
      const el = document.createElement('div');
      el.className = 'pet ' + this.motion;
      el.style.width = this.w + 'px';
      el.style.height = this.h + 'px';
      el.setAttribute('aria-hidden', 'true');
      const bounce = document.createElement('div');
      bounce.className = 'pet-bounce';
      const art = document.createElement('div');
      art.className = 'pet-art';
      if (this.sprite.kind === 'gif') {
        const img = document.createElement('img');
        img.src = this.sprite.url; img.alt = '';
        art.appendChild(img);
      } else {
        art.style.backgroundImage = `url("${this.sprite.url}")`;
        art.style.backgroundSize = `${this.w * this.frames}px ${this.h}px`;
      }
      bounce.appendChild(art);
      el.appendChild(bounce);
      this.el = el; this.art = art;
      this.stage.layer.appendChild(el);
      this.bindEvents();
    }

    bindEvents() {
      let pressTimer = null, start = null;
      const cancel = () => { clearTimeout(pressTimer); pressTimer = null; };
      this.el.addEventListener('pointerdown', e => {
        start = { x: e.clientX, y: e.clientY };
        if (e.pointerType !== 'mouse' && this.stage.opts.removable) {
          pressTimer = setTimeout(() => { start = null; this.stage.remove(this, true); }, 650);
        }
      });
      this.el.addEventListener('pointermove', e => {
        if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) { cancel(); start = null; }
      });
      this.el.addEventListener('pointerup', () => {
        cancel();
        if (start) this.pet();
        start = null;
      });
      this.el.addEventListener('pointercancel', cancel);
      this.el.addEventListener('contextmenu', e => {
        if (!this.stage.opts.removable) return;
        e.preventDefault();
        this.stage.remove(this, true);
      });
    }

    pet() {
      this.el.classList.remove('squish');
      void this.el.offsetWidth;
      this.el.classList.add('squish');
      this.stage.fx(this.x + this.w / 2, this.y, ['♥', '♡', '♥'], 3);
      if (GROUNDED[this.motion] && this.onGround) { this.vy = -260; this.onGround = false; this.vx = 0; }
      if (this.stage.opts.onPet) this.stage.opts.onPet(this);
    }

    groundY(b) { return b.h - this.h - this.stage.opts.ground; }

    update(dt, b, pointer) {
      const maxX = Math.max(0, b.w - this.w);
      this.animating = true;
      this.timer -= dt;

      if (this.motion === 'walk') {
        const gy = this.groundY(b);
        if (!this.onGround) {           // only airborne after being petted
          this.vy += 900 * dt; this.y += this.vy * dt;
          if (this.y >= gy) { this.y = gy; this.vy = 0; this.onGround = true; }
          this.animating = false;
        } else if (this.state === 'move') {
          this.x += this.dir * this.speed * dt;
          if (this.timer <= 0) { this.state = 'rest'; this.timer = rand(0.8, 2.6); }
        } else {
          this.animating = false;
          if (this.timer <= 0) {
            this.state = 'move'; this.timer = rand(2, 6);
            if (Math.random() < 0.4) this.dir *= -1;
          }
        }
        if (this.onGround) this.y = gy;
      } else if (this.motion === 'hop') {
        const gy = this.groundY(b);
        if (this.onGround) {
          this.y = gy;
          if (this.timer <= 0) {
            if (Math.random() < 0.25) this.dir *= -1;
            const lift = Math.sqrt(this.h / 56);
            this.vy = -rand(240, 340) * lift;
            this.vx = this.dir * this.speed;
            this.onGround = false;
          }
        } else {
          this.vy += 900 * dt;
          this.x += this.vx * dt;
          this.y += this.vy * dt;
          if (this.y >= gy) { this.y = gy; this.vy = 0; this.vx = 0; this.onGround = true; this.timer = rand(0.25, 1.1); }
        }
      } else {
        // fly / float / follow: steer toward a target point
        let tx, ty, settle = false;
        if (this.motion === 'follow' && pointer) {
          tx = pointer.x - this.w / 2; ty = pointer.y - this.h - 8;
          settle = Math.hypot(tx - this.x, ty - this.y) < 26;
        } else {
          const ceil = this.motion === 'fly' ? b.h * 0.8 - this.h : b.h - this.h;
          if (!this.target || this.timer <= 0 || Math.hypot(this.target.x - this.x, this.target.y - this.y) < 18) {
            this.target = { x: rand(0, maxX), y: rand(0, Math.max(0, ceil)) };
            this.timer = rand(2, 6);
          }
          tx = this.target.x; ty = this.target.y;
        }
        const dx = tx - this.x, dy = ty - this.y, d = Math.hypot(dx, dy) || 1;
        const sp = settle ? 0 : this.speed;
        const k = Math.min(1, dt * (this.motion === 'float' ? 0.8 : 2.4));
        this.vx += (dx / d * sp - this.vx) * k;
        this.vy += (dy / d * sp - this.vy) * k;
        this.x += this.vx * dt;
        this.y += this.vy * dt;
        if (Math.abs(this.vx) > 6) this.dir = this.vx > 0 ? 1 : -1;
        if (settle && Math.hypot(this.vx, this.vy) < 8) this.animating = false;
      }

      if (this.x < 0) { this.x = 0; this.dir = 1; this.vx = Math.abs(this.vx); }
      if (this.x > maxX) { this.x = maxX; this.dir = -1; this.vx = -Math.abs(this.vx); }
      this.y = Math.min(Math.max(0, this.y), Math.max(0, b.h - this.h));
    }

    render(now) {
      let bob = 0;
      if (this.motion === 'fly') bob = Math.sin((now + this.phase) / 260) * 3;
      if (this.motion === 'float') bob = Math.sin((now + this.phase) / 700) * 6;
      this.el.style.transform = `translate3d(${this.x.toFixed(1)}px,${(this.y + bob).toFixed(1)}px,0)`;
      this.el.classList.toggle('flip', this.dir < 0);
      if (this.frames > 1) {
        const f = this.animating ? Math.floor((now + this.phase) / 1000 * this.fps) % this.frames : 0;
        if (f !== this.lastFrame) {
          this.art.style.backgroundPosition = `${-f * this.w}px 0`;
          this.lastFrame = f;
        }
      }
    }
  }

  /* ── a stage full of pets ──────────────────────────────────── */

  class Stage {
    // el: the box pets live in (use document.body for the whole window).
    // opts: ground (px above the bottom edge), removable, max, passive, onPet, onRemove
    constructor(el, opts) {
      injectStyles();
      this.opts = Object.assign({ ground: 0, removable: true, max: 12, passive: false }, opts || {});
      this.pets = [];
      this.layer = document.createElement('div');
      this.layer.className = 'pets-layer' + (el === document.body ? ' pets-fixed' : '') + (this.opts.passive ? ' passive' : '');
      el.appendChild(this.layer);
      this.pointer = null;
      this.onPointer = e => { this.pointer = { cx: e.clientX, cy: e.clientY }; };
      window.addEventListener('pointermove', this.onPointer, { passive: true });
      this.last = 0;
      this.raf = null;
      this.tick = this.tick.bind(this);
    }

    bounds() { return { w: this.layer.clientWidth, h: this.layer.clientHeight }; }

    async add(sprite, at) {
      const info = await spriteInfo(sprite);
      if (!info.ok || this.destroyed) return null;
      const pet = new Pet(this, sprite, info, at);
      this.pets.push(pet);
      while (this.pets.length > this.opts.max) this.remove(this.pets[0], false);
      this.start();
      return pet;
    }

    remove(pet, withPoof) {
      const i = this.pets.indexOf(pet);
      if (i === -1) return;
      this.pets.splice(i, 1);
      if (withPoof) this.fx(pet.x + pet.w / 2, pet.y + pet.h / 2, ['✦', '✧', '·', '✦', '♡', '·'], 6, true);
      pet.el.remove();
      if (this.opts.onRemove) this.opts.onRemove(pet, withPoof);
    }

    clear() { this.pets.slice().forEach(p => this.remove(p, false)); }

    fx(x, y, chars, n, burst) {
      for (let i = 0; i < n; i++) {
        const s = document.createElement('span');
        s.className = 'pet-fx';
        s.textContent = chars[i % chars.length];
        const ang = burst ? (i / n) * Math.PI * 2 : rand(-0.6, 0.6) - Math.PI / 2;
        const dist = burst ? rand(22, 38) : rand(36, 56);
        s.style.left = x + 'px';
        s.style.top = y + 'px';
        s.style.setProperty('--dx', Math.cos(ang) * dist + 'px');
        s.style.setProperty('--dy', Math.sin(ang) * dist + 'px');
        s.style.animationDelay = (burst ? 0 : i * 0.08) + 's';
        this.layer.appendChild(s);
        setTimeout(() => s.remove(), 1100);
      }
    }

    start() {
      if (this.raf) return;
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.tick);
    }

    stop() { cancelAnimationFrame(this.raf); this.raf = null; }

    tick(now) {
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      const b = this.bounds();
      if (b.w > 0 && b.h > 0) {
        let ptr = null;
        if (this.pointer && this.pets.some(p => p.motion === 'follow')) {
          const r = this.layer.getBoundingClientRect();
          const x = this.pointer.cx - r.left, y = this.pointer.cy - r.top;
          if (x >= 0 && y >= 0 && x <= r.width && y <= r.height) ptr = { x, y };
        }
        for (const p of this.pets) { p.update(dt, b, ptr); p.render(now); }
      }
      this.raf = this.pets.length ? requestAnimationFrame(this.tick) : null;
    }

    destroy() {
      this.destroyed = true;
      this.stop();
      window.removeEventListener('pointermove', this.onPointer);
      this.layer.remove();
    }
  }

  window.Pets = {
    SIZES, SPEEDS, MOTIONS, TEMPLATES,
    templatePixels, templateSprite, pixelsToSheet, fetchSprites, spriteInfo, forget, thumb, Stage,
  };
})();
