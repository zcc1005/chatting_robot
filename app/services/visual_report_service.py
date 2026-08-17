"""把日报快照渲染为紧凑的响应式 HTML 图表页面。"""

from __future__ import annotations

from html import escape

from app.models.daily_report_schemas import DailyReportPreviewResponse


def render_visual_report(
    preview: DailyReportPreviewResponse, *, public_token: str
) -> str:
    title = f"{preview.report_date.isoformat()} 施工日报"
    project_names = [item.project_name for item in preview.projects]
    management = [item.management_count or 0 for item in preview.projects]
    workers = [item.worker_count or 0 for item in preview.projects]
    equipment_names = [item.name for item in preview.equipment]
    equipment_counts = [item.count for item in preview.equipment]
    project_cards = "".join(
        _project_card(item, public_token=public_token, index=index)
        for index, item in enumerate(preview.projects, start=1)
    ) or '<div class="empty">暂无可展示的项目数据</div>'
    unit_line = (
        f'<span class="unit">单位群：{escape(preview.chat_name)}</span>'
        if preview.chat_name
        else ""
    )
    warning_html = "".join(
        f"<li>{escape(item)}</li>" for item in preview.warnings
    ) or "<li>无</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
:root{{--ink:#172033;--muted:#697386;--line:#d9e0e8;--line-soft:#e9edf2;--blue:#2f6db2;--blue-mid:#7fa4cf;--blue-soft:#eaf1f8;--bg:#f2f4f7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;font-size:13px;line-height:1.55}}
.page{{max-width:1180px;margin:auto;padding:14px 14px 24px}}.hero{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:14px 16px 12px;background:#fff;border:1px solid var(--line);border-top:4px solid var(--blue);border-radius:6px}}
.hero-copy{{min-width:0}}h1{{margin:0;font-size:21px;line-height:1.25;letter-spacing:.01em}}.hero-meta{{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:4px;color:var(--muted);font-size:12px}}.unit{{white-space:nowrap}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);margin:10px 0;border:1px solid var(--line);border-radius:6px;background:#fff;overflow:hidden}}.kpi{{min-width:0;padding:9px 14px;border-right:1px solid var(--line-soft)}}.kpi:last-child{{border-right:0}}.kpi-label{{color:var(--muted);font-size:12px}}.kpi-value{{margin-top:1px;font-size:20px;line-height:1.2;font-weight:700;color:#20324a}}
.card{{background:#fff;border:1px solid var(--line);border-radius:6px;padding:13px 14px}}.grid{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr);gap:10px}}h2{{font-size:15px;line-height:1.35;margin:0}}.section-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px;padding-bottom:7px;border-bottom:1px solid var(--line-soft)}}.chart{{width:100%;overflow-x:auto}}svg{{display:block;width:100%;min-width:560px;height:auto}}
.legend{{display:flex;gap:12px;font-size:11px;color:var(--muted);white-space:nowrap}}.dot{{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}}
.projects{{display:grid;gap:10px;margin-top:10px}}.project-title{{display:flex;gap:8px;align-items:center;padding-bottom:7px;border-bottom:1px solid var(--line-soft)}}.badge{{display:inline-grid;place-items:center;width:22px;height:22px;flex:0 0 22px;background:var(--blue-soft);color:var(--blue);border-radius:4px;font-size:12px;font-weight:700}}
.meta{{display:flex;flex-wrap:wrap;gap:0;margin:7px 0 8px;background:#f7f9fb;border:1px solid var(--line-soft)}}.pill{{padding:4px 10px;color:#3d4c60;border-right:1px solid var(--line-soft)}}.pill:last-child{{border-right:0}}h3{{margin:8px 0 2px;font-size:12px;color:#44536a}}p{{margin:2px 0 5px}}ul{{margin:3px 0;padding-left:20px;line-height:1.6}}
.photos{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:8px}}.photos img{{display:block;width:100%;height:156px;object-fit:cover;border:1px solid var(--line-soft);border-radius:3px;background:#e8edf3}}
.empty{{color:var(--muted);padding:16px;text-align:center}}.notice{{margin-top:10px}}.foot{{margin-top:8px;color:var(--muted);font-size:11px;text-align:right}}
@media(max-width:780px){{.page{{padding:8px}}.hero{{align-items:flex-start;flex-direction:column;gap:3px}}.kpis{{grid-template-columns:repeat(2,1fr)}}.kpi:nth-child(2){{border-right:0}}.kpi:nth-child(-n+2){{border-bottom:1px solid var(--line-soft)}}.grid{{grid-template-columns:1fr}}.photos{{grid-template-columns:1fr 1fr}}}}
@media(max-width:480px){{.photos{{grid-template-columns:1fr}}.section-head{{align-items:flex-start;flex-direction:column;gap:4px}}h1{{font-size:19px}}}}
</style></head><body><main class="page report-shell">
<section class="hero"><div class="hero-copy"><h1>{escape(title)}</h1><div class="hero-meta"><span>日报汇总</span>{unit_line}</div></div><div class="hero-meta">数据日期：{preview.report_date.isoformat()}</div></section>
<section class="kpis">
{_kpi("项目数量", str(preview.project_count))}
{_kpi("管理人员", _people(preview.management_total))}
{_kpi("施工人员", _people(preview.worker_total))}
{_kpi("机械种类", str(len(preview.equipment)))}
</section>
<section class="grid">
<div class="card"><div class="section-head"><h2>各项目人员分布</h2><div class="legend"><span><i class="dot" style="background:#7fa4cf"></i>管理人员</span><span><i class="dot" style="background:#2f6db2"></i>施工人员</span></div></div><div class="chart">{_grouped_bar_svg(project_names, management, workers)}</div></div>
<div class="card"><div class="section-head"><h2>机械设备数量</h2><div class="legend">单位：台/套</div></div><div class="chart">{_single_bar_svg(equipment_names, equipment_counts)}</div></div>
</section>
<section class="projects">{project_cards}</section>
<section class="card notice"><div class="section-head"><h2>待确认和提醒</h2></div><ul>{warning_html}</ul></section>
<div class="foot">施工日报机器人 · 页面数据来自本次已保存的汇总快照</div>
</main></body></html>"""


def _project_card(project, *, public_token: str, index: int) -> str:
    work = "".join(
        "<li>"
        + (f"{escape(item.location)}：" if item.location else "")
        + escape(item.content)
        + (f"（进度：{escape(item.progress)}）" if item.progress else "")
        + "</li>"
        for item in project.work_items
    ) or "<li>暂无</li>"
    photos = "".join(
        f'<img loading="lazy" alt="{escape(project.project_name)}现场图" '
        f'src="/visual-reports/{escape(public_token)}/images/{image.attachment_id}">'
        for image in project.images
    )
    photo_block = f'<div class="photos">{photos}</div>' if photos else ""
    return f"""<article class="card">
<div class="project-title"><span class="badge">{index}</span><h2>{escape(project.project_name)}</h2></div>
<div class="meta"><span class="pill">管理人员：{_people(project.management_count)}</span><span class="pill">施工人员：{_people(project.worker_count)}</span><span class="pill">天气：{escape(project.weather or '待确认')}</span></div>
<h3>今日施工</h3><ul>{work}</ul>
<h3>明日计划</h3><p>{escape(project.tomorrow_plan or '待确认')}</p>
<p><strong>安全：</strong>{escape(project.safety_status or '待确认')}　<strong>质量：</strong>{escape(project.quality_status or '待确认')}</p>
{photo_block}</article>"""


def _kpi(label: str, value: str) -> str:
    return f'<div class="kpi"><div class="kpi-label">{escape(label)}</div><div class="kpi-value">{escape(value)}</div></div>'


def _grouped_bar_svg(labels: list[str], first: list[int], second: list[int]) -> str:
    if not labels:
        return '<div class="empty">暂无数据</div>'
    maximum = max(first + second + [1])
    row = 52
    height = 12 + row * len(labels)
    chart_x = 260
    chart_width = 410
    parts = [f'<svg viewBox="0 0 740 {height}" role="img" aria-label="项目人员数量图表">']
    for index, (label, one, two) in enumerate(zip(labels, first, second, strict=True)):
        y = 7 + index * row
        one_width = int(chart_width * one / maximum)
        two_width = int(chart_width * two / maximum)
        parts.append(_svg_project_label(label, y))
        parts.append(f'<line x1="{chart_x}" y1="{y + 44}" x2="{chart_x + chart_width}" y2="{y + 44}" stroke="#edf1f5"/>')
        parts.append(f'<rect x="{chart_x}" y="{y}" width="{one_width}" height="12" rx="2" fill="#7fa4cf"/><text x="{chart_x + one_width + 7}" y="{y + 10}" font-size="11" fill="#34445a">{one}</text>')
        parts.append(f'<rect x="{chart_x}" y="{y + 20}" width="{two_width}" height="12" rx="2" fill="#2f6db2"/><text x="{chart_x + two_width + 7}" y="{y + 30}" font-size="11" fill="#34445a">{two}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _single_bar_svg(labels: list[str], values: list[int]) -> str:
    if not labels:
        return '<div class="empty">暂无机械数据</div>'
    maximum = max(values + [1])
    row = 32
    height = 8 + row * len(labels)
    chart_x = 155
    chart_width = 355
    parts = [f'<svg viewBox="0 0 550 {height}" role="img" aria-label="机械设备数量图表">']
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        y = 6 + index * row
        width = int(chart_width * value / maximum)
        parts.append(f'<text x="0" y="{y + 12}" font-size="12" fill="#34445a">{escape(_short(label, 10))}</text><rect x="{chart_x}" y="{y}" width="{width}" height="14" rx="2" fill="#4f82bb"/><text x="{chart_x + width + 7}" y="{y + 12}" font-size="11" fill="#34445a">{value}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_project_label(value: str, y: int) -> str:
    """把项目名限制在独立标签列，避免侵入右侧柱状图。"""

    line_limit = 18
    first = value[:line_limit]
    second = value[line_limit : line_limit * 2]
    if len(value) > line_limit * 2:
        second = second[:-1] + "…"
    title = f"<title>{escape(value)}</title>"
    if not second:
        return (
            f'<text x="0" y="{y + 22}" font-size="12" fill="#27364a">'
            f"{title}{escape(first)}</text>"
        )
    return (
        f'<text x="0" y="{y + 13}" font-size="12" fill="#27364a">{title}'
        f'<tspan x="0" dy="0">{escape(first)}</tspan>'
        f'<tspan x="0" dy="16">{escape(second)}</tspan></text>'
    )


def _people(value: int | None) -> str:
    return "待确认" if value is None else f"{value} 人"


def _short(value: str, limit: int = 16) -> str:
    return value if len(value) <= limit else value[:limit] + "…"
