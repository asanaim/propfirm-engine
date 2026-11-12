import { useCallback, useEffect, useRef, useState } from "react";
import { Menu as RaMenu, usePermissions } from "react-admin";
import { Terrain } from "./components/Terrain";
import { ICONS, SIZE, STROKE } from "./icons";
import { screensFor } from "./screens";

/** Numbers each menu item (--i) for the staggered entrance. */
function useStagger(root: React.RefObject<HTMLElement | null>, count: number): void {
  useEffect(() => {
    const items = root.current?.querySelectorAll<HTMLElement>(".RaMenuItemLink-root") ?? [];
    items.forEach((item, i) => {
      item.style.setProperty("--i", i.toString());
    });
  }, [root, count]);
}

/** The left menu in the pipridge style: the menu itself stays still; only
the item under the pointer moves, lifting toward the reader with its icon
turning, and items rise in from depth when the page opens. The ridge
terrain drifts at the foot (all still for reduced motion). */
export function Menu() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  const area = useRef<HTMLDivElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const [webgl, setWebgl] = useState(true);
  const noWebgl = useCallback(() => {
    setWebgl(false);
  }, []);
  useStagger(plane, screens.size);
  return (
    <div className="menu-3d" ref={area}>
      {webgl ? <Terrain area={area} onUnavailable={noWebgl} /> : <div className="menu-ridge-fallback" aria-hidden="true" />}
      <div className="menu-3d-plane" ref={plane}>
        <MenuItems screens={screens} />
      </div>
    </div>
  );
}

function MenuItems({ screens }: { screens: Set<string> }) {
  return (
    <RaMenu>
      <RaMenu.DashboardItem primaryText="My work" leftIcon={<ICONS.work size={SIZE} strokeWidth={STROKE} />} />
      {screens.has("traders") ? <RaMenu.Item to="/traders" primaryText="Traders" leftIcon={<ICONS.traders size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("transactions") ? (
        <RaMenu.Item to="/transactions" primaryText="Transactions and emails" leftIcon={<ICONS.transactions size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      <RaMenu.ResourceItems />
      {screens.has("settings") ? (
        <RaMenu.Item to="/settings" primaryText="System settings" leftIcon={<ICONS.settings size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("news") ? <RaMenu.Item to="/news" primaryText="News desk" leftIcon={<ICONS.news size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("anomalies") ? (
        <RaMenu.Item to="/anomalies" primaryText="Anomaly report" leftIcon={<ICONS.anomalies size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("audit") ? <RaMenu.Item to="/audit" primaryText="Security log" leftIcon={<ICONS.audit size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("methods") ? (
        <RaMenu.Item to="/payment-methods" primaryText="Payment methods" leftIcon={<ICONS.methods size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("site") ? <RaMenu.Item to="/site" primaryText="Website" leftIcon={<ICONS.site size={SIZE} strokeWidth={STROKE} />} /> : null}
      {screens.has("templates") ? (
        <RaMenu.Item to="/notices" primaryText="Notification templates" leftIcon={<ICONS.templates size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
      {screens.has("policies") ? (
        <RaMenu.Item to="/policies" primaryText="Policy pages" leftIcon={<ICONS.policies size={SIZE} strokeWidth={STROKE} />} />
      ) : null}
    </RaMenu>
  );
}
