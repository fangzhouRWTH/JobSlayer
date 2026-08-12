import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface EChartProps {
  option: EChartsCoreOption;
  height: number;
  label: string;
}

export function EChart({ option, height, label }: EChartProps) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const chart = echarts.init(host.current, undefined, { renderer: "canvas" });
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);

  return <div ref={host} style={{ height }} role="img" aria-label={label} />;
}
