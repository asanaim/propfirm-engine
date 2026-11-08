/* pipridge sign in: the ridge terrain behind the card, and the card leaning
   toward the pointer. The same drawing as the portal's hero
   (portal/src/components/Terrain.tsx): ridge lines rising to the amber
   summit, drawn with WebGL, no library. It draws only while the tab is
   visible, at most at device pixel ratio 2; one still frame for people who
   prefer reduced motion; nothing (CSS ridges instead) without WebGL. */
(function () {
  "use strict";

  var ROWS = 44;
  var SEGMENTS = 200;

  var VERTEX = [
    "attribute vec2 a_grid;",
    "uniform mat4 u_view;",
    "uniform float u_time;",
    "varying float v_x;",
    "varying float v_z;",
    "varying float v_h;",
    "float height(float x, float z, float t) {",
    "  float rise = 0.62 * smoothstep(0.0, 1.0, x);",
    "  float ridges = 0.10 * sin(x * 9.0 + z * 3.1 + t * 0.35)",
    "    + 0.05 * sin(x * 23.0 - z * 6.0 + t * 0.55)",
    "    + 0.03 * sin(x * 41.0 + z * 11.0 - t * 0.4);",
    "  float peak = 0.28 * exp(-pow((x - 0.92) * 7.0, 2.0) - pow((z - 0.15) * 4.0, 2.0));",
    "  return (rise + ridges) * (1.0 - 0.45 * z) + peak;",
    "}",
    "void main() {",
    "  float x = a_grid.x;",
    "  float z = a_grid.y;",
    "  float h = height(x, z, u_time);",
    "  v_x = x; v_z = z; v_h = h;",
    "  gl_Position = u_view * vec4((x - 0.5) * 4.4, h - 0.35, -z * 3.2, 1.0);",
    "}",
  ].join("\n");

  var FRAGMENT = [
    "precision mediump float;",
    "uniform vec3 u_low;",
    "uniform vec3 u_high;",
    "uniform float u_alpha;",
    "varying float v_x;",
    "varying float v_z;",
    "varying float v_h;",
    "void main() {",
    "  float t = clamp(v_h * 1.15 + v_x * 0.25, 0.0, 1.0);",
    "  vec3 colour = mix(u_low, u_high, smoothstep(0.35, 1.0, t));",
    "  float edge = smoothstep(0.0, 0.12, v_x) * smoothstep(1.0, 0.82, v_x);",
    "  float depth = 1.0 - v_z * 0.85;",
    "  gl_FragColor = vec4(colour, u_alpha * edge * depth);",
    "}",
  ].join("\n");

  function reduced() {
    return typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function perspective(fovy, aspect, near, far) {
    var f = 1 / Math.tan(fovy / 2);
    var nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  }

  function lookAt(eye, target) {
    var zx = eye[0] - target[0];
    var zy = eye[1] - target[1];
    var zz = eye[2] - target[2];
    var zl = Math.hypot(zx, zy, zz);
    zx /= zl;
    zy /= zl;
    zz /= zl;
    var xx = zz;
    var xz = -zx;
    var xl = Math.hypot(xx, xz);
    xx /= xl;
    xz /= xl;
    var yx = zy * xz;
    var yy = zz * xx - zx * xz;
    var yz = -zy * xx;
    return new Float32Array([
      xx, yx, zx, 0,
      0, yy, zy, 0,
      xz, yz, zz, 0,
      -(xx * eye[0] + xz * eye[2]), -(yx * eye[0] + yy * eye[1] + yz * eye[2]), -(zx * eye[0] + zy * eye[1] + zz * eye[2]), 1,
    ]);
  }

  function multiply(a, b) {
    var out = new Float32Array(16);
    for (var c = 0; c < 4; c++) {
      for (var r = 0; r < 4; r++) {
        var sum = 0;
        for (var k = 0; k < 4; k++) sum += a[k * 4 + r] * b[c * 4 + k];
        out[c * 4 + r] = sum;
      }
    }
    return out;
  }

  function rgb(hex) {
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
    if (m === null) return null;
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
  }

  function lines() {
    var out = new Float32Array(ROWS * SEGMENTS * 4);
    var i = 0;
    for (var r = 0; r < ROWS; r++) {
      var z = r / (ROWS - 1);
      for (var s = 0; s < SEGMENTS; s++) {
        out[i++] = s / SEGMENTS;
        out[i++] = z;
        out[i++] = (s + 1) / SEGMENTS;
        out[i++] = z;
      }
    }
    return out;
  }

  function compile(gl, type, source) {
    var shader = gl.createShader(type);
    if (shader === null) return null;
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
  }

  /* the card leans toward the pointer (sets --rx, --ry, --mx, --my) */
  function tilt(card) {
    if (reduced()) return;
    var max = 4;
    var frame = 0;
    document.addEventListener(
      "pointermove",
      function (e) {
        if (e.pointerType === "touch") return;
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(function () {
          var r = card.getBoundingClientRect();
          var x = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
          var y = Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1);
          card.style.setProperty("--ry", ((x - 0.5) * 2 * max).toFixed(2) + "deg");
          card.style.setProperty("--rx", ((0.5 - y) * 2 * max).toFixed(2) + "deg");
          card.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
          card.style.setProperty("--my", (y * 100).toFixed(1) + "%");
        });
      },
      { passive: true },
    );
    document.addEventListener("pointerleave", function () {
      ["--rx", "--ry", "--mx", "--my"].forEach(function (p) {
        card.style.removeProperty(p);
      });
    });
  }

  function terrain() {
    var canvas = document.createElement("canvas");
    canvas.className = "pp-terrain";
    canvas.setAttribute("aria-hidden", "true");
    var gl = typeof window.WebGLRenderingContext === "undefined" ? null : canvas.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: false });
    var vs = gl && compile(gl, gl.VERTEX_SHADER, VERTEX);
    var fs = gl && compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    var program = gl ? gl.createProgram() : null;
    if (!gl || !vs || !fs || !program) {
      document.body.classList.add("pp-no-webgl");
      return;
    }
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      document.body.classList.add("pp-no-webgl");
      return;
    }
    document.body.insertBefore(canvas, document.body.firstChild);
    gl.useProgram(program);
    var grid = lines();
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, grid, gl.STATIC_DRAW);
    var aGrid = gl.getAttribLocation(program, "a_grid");
    gl.enableVertexAttribArray(aGrid);
    gl.vertexAttribPointer(aGrid, 2, gl.FLOAT, false, 0, 0);
    var uView = gl.getUniformLocation(program, "u_view");
    var uTime = gl.getUniformLocation(program, "u_time");
    var uLow = gl.getUniformLocation(program, "u_low");
    var uHigh = gl.getUniformLocation(program, "u_high");
    var uAlpha = gl.getUniformLocation(program, "u_alpha");
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    var still = reduced();
    var colours = { low: [0.1, 0.27, 0.32], high: [0.95, 0.66, 0.23], alpha: 0.8 };
    function readColours() {
      var css = getComputedStyle(document.documentElement);
      var dark = document.documentElement.classList.contains("pf-v5-theme-dark");
      colours = {
        low: rgb(css.getPropertyValue("--pp-ridge")) || [0.1, 0.27, 0.32],
        high: rgb(css.getPropertyValue("--pp-summit")) || [0.95, 0.66, 0.23],
        alpha: dark ? 0.9 : 0.8,
      };
    }
    readColours();
    new MutationObserver(readColours).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    function size() {
      var ratio = Math.min(window.devicePixelRatio || 1, 2);
      var w = Math.max(1, Math.round(canvas.clientWidth * ratio));
      var h = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      gl.viewport(0, 0, w, h);
    }

    var aim = { x: 0, y: 0, tx: 0, ty: 0 };
    document.addEventListener(
      "pointermove",
      function (e) {
        aim.tx = (e.clientX / window.innerWidth - 0.5) * 2;
        aim.ty = (e.clientY / window.innerHeight - 0.5) * 2;
      },
      { passive: true },
    );

    var start = performance.now();
    var frame = 0;
    function draw(now) {
      size();
      var t = still ? 6 : (now - start) / 1000;
      aim.x += (aim.tx - aim.x) * 0.06;
      aim.y += (aim.ty - aim.y) * 0.06;
      var yaw = Math.sin(t * 0.12) * 0.18 + aim.x * 0.3;
      var eye = [Math.sin(yaw) * 2.3, 0.95 - aim.y * 0.2, Math.cos(yaw) * 2.3];
      var view = multiply(perspective(0.8, canvas.width / canvas.height, 0.1, 20), lookAt(eye, [0, 0.42, -1.4]));
      gl.uniformMatrix4fv(uView, false, view);
      gl.uniform1f(uTime, t);
      gl.uniform3fv(uLow, colours.low);
      gl.uniform3fv(uHigh, colours.high);
      gl.uniform1f(uAlpha, colours.alpha);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.LINES, 0, grid.length / 2);
    }
    function loop() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function tick(now) {
        if (document.hidden) return;
        draw(now);
        frame = requestAnimationFrame(tick);
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !still) loop();
    });
    if (still) requestAnimationFrame(draw);
    else loop();
  }

  function start() {
    terrain();
    var card = document.querySelector(".pf-v5-c-login__main");
    if (card) tilt(card);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
