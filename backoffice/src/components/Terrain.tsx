/** The hero's 3D terrain: ridge lines rising toward the summit, drawn with
WebGL (no library: a few kilobytes, so the first paint stays fast).

It drifts on its own, turns toward the pointer or a finger, and tilts as the
page scrolls, so it moves on touch screens too. It draws only while on
screen and the tab is visible, at most at device pixel ratio 2. For people
who prefer reduced motion it draws one still frame. Colours come from the
theme (--ridge-start, --summit) and follow a theme change. Without WebGL it
renders nothing and `onUnavailable` is called (the CSS ridges stand in). */

import { type RefObject, useEffect, useRef } from "react";

const ROWS = 44; // ridge lines, near to far
const SEGMENTS = 200; // points along each line

const VERTEX = `
attribute vec2 a_grid;
uniform mat4 u_view;
uniform float u_time;
varying float v_x;
varying float v_z;
varying float v_h;

float height(float x, float z, float t) {
  float rise = 0.62 * smoothstep(0.0, 1.0, x);
  float ridges = 0.10 * sin(x * 9.0 + z * 3.1 + t * 0.35)
    + 0.05 * sin(x * 23.0 - z * 6.0 + t * 0.55)
    + 0.03 * sin(x * 41.0 + z * 11.0 - t * 0.4);
  float peak = 0.28 * exp(-pow((x - 0.92) * 7.0, 2.0) - pow((z - 0.15) * 4.0, 2.0));
  return (rise + ridges) * (1.0 - 0.45 * z) + peak;
}

void main() {
  float x = a_grid.x;
  float z = a_grid.y;
  float h = height(x, z, u_time);
  v_x = x;
  v_z = z;
  v_h = h;
  gl_Position = u_view * vec4((x - 0.5) * 4.4, h - 0.35, -z * 3.2, 1.0);
}
`;

const FRAGMENT = `
precision mediump float;
uniform vec3 u_low;
uniform vec3 u_high;
uniform float u_alpha;
varying float v_x;
varying float v_z;
varying float v_h;

void main() {
  float t = clamp(v_h * 1.15 + v_x * 0.25, 0.0, 1.0);
  vec3 colour = mix(u_low, u_high, smoothstep(0.35, 1.0, t));
  float edge = smoothstep(0.0, 0.12, v_x) * smoothstep(1.0, 0.82, v_x);
  float depth = 1.0 - v_z * 0.85;
  gl_FragColor = vec4(colour, u_alpha * edge * depth);
}
`;

type Mat4 = Float32Array;

function perspective(fovy: number, aspect: number, near: number, far: number): Mat4 {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
}

function lookAt(eye: [number, number, number], target: [number, number, number]): Mat4 {
  const [ex, ey, ez] = eye;
  let zx = ex - target[0];
  let zy = ey - target[1];
  let zz = ez - target[2];
  const zl = Math.hypot(zx, zy, zz);
  zx /= zl;
  zy /= zl;
  zz /= zl;
  // x = up(0,1,0) × z
  let xx = zz;
  let xz = -zx;
  const xl = Math.hypot(xx, xz);
  xx /= xl;
  xz /= xl;
  // y = z × x
  const yx = zy * xz;
  const yy = zz * xx - zx * xz;
  const yz = -zy * xx;
  return new Float32Array([
    xx, yx, zx, 0,
    0, yy, zy, 0,
    xz, yz, zz, 0,
    -(xx * ex + xz * ez), -(yx * ex + yy * ey + yz * ez), -(zx * ex + zy * ey + zz * ez), 1,
  ]); // prettier-ignore
}

function multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += (a[k * 4 + r] ?? 0) * (b[c * 4 + k] ?? 0);
      out[c * 4 + r] = sum;
    }
  }
  return out;
}

/** "#1a4651" → [0.10, 0.27, 0.32]; anything else → the fallback. */
export function rgb(value: string, fallback: [number, number, number]): [number, number, number] {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value.trim());
  if (m === null) return fallback;
  return [1, 2, 3].map((i) => parseInt(m[i] ?? "0", 16) / 255) as [number, number, number];
}

/** The grid of ridge lines, as pairs of points for gl.LINES. */
export function lines(rows: number, segments: number): Float32Array {
  const out = new Float32Array(rows * segments * 4);
  let i = 0;
  for (let r = 0; r < rows; r++) {
    const z = r / (rows - 1);
    for (let s = 0; s < segments; s++) {
      out[i++] = s / segments;
      out[i++] = z;
      out[i++] = (s + 1) / segments;
      out[i++] = z;
    }
  }
  return out;
}

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (shader === null) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
}

export function Terrain({
  area,
  onUnavailable,
}: {
  area?: RefObject<HTMLElement | null> | undefined;
  onUnavailable: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current;
    if (el === null) return;
    if (typeof window.WebGLRenderingContext === "undefined") {
      onUnavailable();
      return;
    }
    const gl = el.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: false });
    const vs = gl && compile(gl, gl.VERTEX_SHADER, VERTEX);
    const fs = gl && compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    const program = gl?.createProgram() ?? null;
    if (gl === null || vs === null || fs === null || program === null) {
      onUnavailable();
      return;
    }
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      onUnavailable();
      return;
    }
    gl.useProgram(program);
    const grid = lines(ROWS, SEGMENTS);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, grid, gl.STATIC_DRAW);
    const aGrid = gl.getAttribLocation(program, "a_grid");
    gl.enableVertexAttribArray(aGrid);
    gl.vertexAttribPointer(aGrid, 2, gl.FLOAT, false, 0, 0);
    const uView = gl.getUniformLocation(program, "u_view");
    const uTime = gl.getUniformLocation(program, "u_time");
    const uLow = gl.getUniformLocation(program, "u_low");
    const uHigh = gl.getUniformLocation(program, "u_high");
    const uAlpha = gl.getUniformLocation(program, "u_alpha");
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let colours = { low: [0.1, 0.27, 0.32], high: [0.95, 0.66, 0.23], alpha: 0.5 };
    const readColours = () => {
      const css = getComputedStyle(document.documentElement);
      const dark = document.documentElement.dataset.theme === "dark";
      colours = {
        low: rgb(css.getPropertyValue("--ridge-start"), [0.1, 0.27, 0.32]),
        high: rgb(css.getPropertyValue("--summit"), [0.95, 0.66, 0.23]),
        alpha: dark ? 0.9 : 0.62,
      };
    };
    readColours();
    const themeWatch = new MutationObserver(readColours);
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const size = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(el.clientWidth * ratio));
      const h = Math.max(1, Math.round(el.clientHeight * ratio));
      if (el.width !== w || el.height !== h) {
        el.width = w;
        el.height = h;
      }
      gl.viewport(0, 0, w, h);
    };
    const sizer = new ResizeObserver(size);
    sizer.observe(el);

    // pointer (mouse, pen or finger) aims the camera; eased toward each frame
    const aim = { x: 0, y: 0, tx: 0, ty: 0 };
    const zone = area?.current ?? el;
    const point = (e: PointerEvent) => {
      const r = zone.getBoundingClientRect();
      aim.tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
      aim.ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
    };
    const release = () => {
      aim.tx = 0;
      aim.ty = 0;
    };
    zone.addEventListener("pointermove", point, { passive: true });
    zone.addEventListener("pointerdown", point, { passive: true });
    zone.addEventListener("pointerleave", release, { passive: true });

    let visible = true;
    const seen = new IntersectionObserver((entries) => {
      visible = entries.some((e) => e.isIntersecting);
      if (visible && !still) loop();
    });
    seen.observe(el);

    const start = performance.now();
    let frame = 0;
    const draw = (now: number) => {
      size();
      const t = still ? 6 : (now - start) / 1000;
      aim.x += (aim.tx - aim.x) * 0.06;
      aim.y += (aim.ty - aim.y) * 0.06;
      const r = el.getBoundingClientRect();
      const scroll = Math.min(Math.max(1 - r.bottom / (window.innerHeight + r.height), 0), 1);
      const yaw = Math.sin(t * 0.12) * 0.18 + aim.x * 0.35;
      const eye: [number, number, number] = [Math.sin(yaw) * 2.3, 0.95 + aim.y * -0.2 + scroll * 0.5, Math.cos(yaw) * 2.3];
      const view = multiply(perspective(0.8, el.width / el.height, 0.1, 20), lookAt(eye, [0, 0.42, -1.4]));
      gl.uniformMatrix4fv(uView, false, view);
      gl.uniform1f(uTime, t);
      gl.uniform3fv(uLow, colours.low);
      gl.uniform3fv(uHigh, colours.high);
      gl.uniform1f(uAlpha, colours.alpha);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.LINES, 0, grid.length / 2);
    };
    function loop() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function tick(now) {
        if (!visible || document.hidden) return;
        draw(now);
        frame = requestAnimationFrame(tick);
      });
    }
    const resume = () => {
      if (!document.hidden && visible && !still) loop();
    };
    document.addEventListener("visibilitychange", resume);
    if (still) requestAnimationFrame(draw);
    else loop();
    return () => {
      cancelAnimationFrame(frame);
      themeWatch.disconnect();
      sizer.disconnect();
      seen.disconnect();
      document.removeEventListener("visibilitychange", resume);
      zone.removeEventListener("pointermove", point);
      zone.removeEventListener("pointerdown", point);
      zone.removeEventListener("pointerleave", release);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, [area, onUnavailable]);
  return <canvas ref={canvas} className="terrain" aria-hidden="true" />;
}
