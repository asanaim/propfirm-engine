import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Terrain, lines, rgb } from "./Terrain";

describe("Terrain", () => {
  it("reads theme colours", () => {
    expect(rgb("#ff0080", [0, 0, 0])).toEqual([1, 0, 128 / 255]);
    expect(rgb(" #0F2B33 ", [0, 0, 0])).toEqual([15 / 255, 43 / 255, 51 / 255]);
    expect(rgb("rgb(1 2 3)", [0.5, 0.5, 0.5])).toEqual([0.5, 0.5, 0.5]);
  });

  it("builds one line per row from segment pairs", () => {
    const g = lines(3, 4);
    expect(g.length).toBe(3 * 4 * 4);
    expect(Array.from(g.slice(0, 4))).toEqual([0, 0, 0.25, 0]);
    expect(Array.from(g.slice(-4))).toEqual([0.75, 1, 1, 1]);
  });

  it("hands over to the CSS ridges without WebGL", () => {
    const unavailable = vi.fn();
    render(<Terrain onUnavailable={unavailable} />);
    expect(unavailable).toHaveBeenCalledOnce();
  });
});
