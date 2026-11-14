import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { navigate, usePath } from "./router";

function Where() {
  return <p>{usePath()}</p>;
}

/** Gives the document a view transition API, or takes it away (jsdom has none). */
function transitions(start: ((update: () => void) => void) | undefined) {
  if (start === undefined) Reflect.deleteProperty(document, "startViewTransition");
  else Object.defineProperty(document, "startViewTransition", { value: start, configurable: true, writable: true });
}

function motion(reduce: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: reduce });
}

afterEach(() => {
  transitions(undefined);
  window.history.pushState(null, "", "/");
});

describe("moving between portal pages", () => {
  it("changes the page inside a view transition where the browser has one", () => {
    motion(false);
    const start = vi.fn((update: () => void) => {
      update();
    });
    transitions(start);
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    render(<Where />);
    act(() => {
      navigate("/tools");
    });
    expect(start).toHaveBeenCalledTimes(1);
    expect(screen.getByText("/tools")).toBeInTheDocument();
    expect(scroll).toHaveBeenCalledWith({ top: 0, left: 0 });
  });

  it("changes at once for reduced motion, and without the API", () => {
    motion(true);
    const start = vi.fn();
    transitions(start);
    vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    render(<Where />);
    act(() => {
      navigate("/shop");
    });
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByText("/shop")).toBeInTheDocument();
    transitions(undefined);
    motion(false);
    act(() => {
      navigate("/news");
    });
    expect(screen.getByText("/news")).toBeInTheDocument();
  });

  it("lands on a named section instead of the top", () => {
    motion(false);
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const section = document.createElement("section");
    section.id = "how";
    const into = vi.fn();
    section.scrollIntoView = into;
    document.body.append(section);
    render(<Where />);
    act(() => {
      navigate("/#how");
    });
    expect(into).toHaveBeenCalled();
    expect(scroll).not.toHaveBeenCalled();
    section.remove();
  });
});

describe("moving waits for the next page's code", () => {
  it("moves at once when the page is ready, after it arrives otherwise", async () => {
    const { navigate, setPrepare } = await import("./router");
    window.history.pushState(null, "", "/");
    let arrive: () => void = () => undefined;
    setPrepare((path) => (path === "/slow" ? new Promise<void>((r) => { arrive = r; }) : null));
    navigate("/fast");
    expect(window.location.pathname).toBe("/fast");
    navigate("/slow");
    expect(window.location.pathname).toBe("/fast"); // still fetching
    arrive();
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/slow");
    setPrepare(() => null);
  });
});
