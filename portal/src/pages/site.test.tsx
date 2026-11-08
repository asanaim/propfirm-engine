import { screen } from "@testing-library/react";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { OverviewPage } from "./OverviewPage";

const anonymous = () => fakeAuth({ status: "anonymous", session: null });

describe("the home page text (rules AC07, AC08)", () => {
  it("shows the text the firm approved, figures already filled", async () => {
    const site = {
      hero: {
        fields: { title_line_1: "Your edge, funded.", title_line_2: "Keep up to 90% of the profit.", lead: "From $59.", button: "Start now" },
        items: [],
      },
      how: {
        fields: { heading: "Three steps", footnote: "Simulated trading." },
        items: [
          { title: "Pick", text: "Choose a size." },
          { title: "Pass", text: "Hit the target." },
        ],
      },
    };
    renderWith(<OverviewPage />, anonymous(), fakeFetch([["GET", "/catalogue", 200, []], ["GET", "/site", 200, site]]));
    const title = await screen.findByRole("heading", { level: 1 });
    expect(title).toHaveTextContent("Your edge, funded.");
    expect(title).toHaveTextContent("Keep up to 90% of the profit.");
    expect(screen.getByRole("link", { name: "Start now" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Three steps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pass" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Get paid" })).toBeNull();
  });

  it("keeps its own words where nothing was edited", async () => {
    renderWith(<OverviewPage />, anonymous(), fakeFetch([["GET", "/catalogue", 200, []], ["GET", "/site", 200, {}]]));
    expect(await screen.findByRole("heading", { name: "How it works" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Get paid" })).toBeInTheDocument();
  });
});
