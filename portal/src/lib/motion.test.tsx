import { act, fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useReveal, useTilt } from "./motion";

function media(matches: (q: string) => boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: matches(q), media: q, addEventListener() {}, removeEventListener() {} }));
}

function Tilted() {
  const ref = useRef<HTMLDivElement>(null);
  useTilt(ref, 10);
  return <div data-testid="t" ref={ref} />;
}

function Revealed() {
  const ref = useRef<HTMLDivElement>(null);
  useReveal(ref, []);
  return (
    <div ref={ref}>
      <p data-testid="r" data-reveal="">
        text
      </p>
    </div>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useTilt", () => {
  it("tilts toward the pointer and rests on leave", () => {
    media((q) => q.includes("hover"));
    vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => {
      f(0);
      return 1;
    });
    const { getByTestId } = render(<Tilted />);
    const el = getByTestId("t");
    el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
    act(() => {
      fireEvent.pointerMove(el, { clientX: 100, clientY: 0 });
    });
    expect(el.style.getPropertyValue("--ry")).toBe("10.00deg");
    expect(el.style.getPropertyValue("--rx")).toBe("10.00deg");
    expect(el.dataset.tilt).toBe("on");
    act(() => {
      fireEvent.pointerLeave(el);
    });
    expect(el.style.getPropertyValue("--ry")).toBe("");
    expect(el.dataset.tilt).toBeUndefined();
  });

  it("does nothing for reduced motion", () => {
    media((q) => q.includes("reduce") || q.includes("hover"));
    const { getByTestId } = render(<Tilted />);
    const el = getByTestId("t");
    fireEvent.pointerMove(el, { clientX: 10, clientY: 10 });
    expect(el.dataset.tilt).toBeUndefined();
  });
});

describe("useReveal", () => {
  it("leaves content visible without IntersectionObserver", () => {
    media(() => false);
    vi.stubGlobal("IntersectionObserver", undefined);
    const { getByTestId } = render(<Revealed />);
    expect(getByTestId("r").dataset.reveal).toBe("");
  });

  it("hides until seen, then reveals", () => {
    media(() => false);
    let fire: (entries: { isIntersecting: boolean; target: Element }[]) => void = () => undefined;
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(cb: typeof fire) {
          fire = cb;
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const { getByTestId } = render(<Revealed />);
    const el = getByTestId("r");
    expect(el.dataset.reveal).toBe("wait");
    act(() => {
      fire([{ isIntersecting: true, target: el }]);
    });
    expect(el.dataset.reveal).toBe("in");
  });
});
