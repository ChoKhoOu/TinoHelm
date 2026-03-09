//! F3 — Strategy workspace: master-detail split view.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Style,
    text::{Line, Span},
    widgets::{Block, Borders, Cell, Paragraph, Row, Table},
    Frame,
};

use crate::tui::app::{App, PanelFocus};
use crate::tui::theme;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(area);

    render_list(f, cols[0], app);
    render_detail(f, cols[1], app);
}

fn render_list(f: &mut Frame, area: Rect, app: &App) {
    let is_focused = app.panel_focus == PanelFocus::Left;
    let border_style = if is_focused {
        theme::style_border_focused()
    } else {
        theme::style_border()
    };

    let title = if app.strategy_loading {
        " STRATEGIES (loading\u{2026}) "
    } else {
        " STRATEGIES "
    };

    let header = Row::new(vec![
        Cell::from("Name"),
        Cell::from("Type"),
        Cell::from("Class"),
    ])
    .style(theme::style_header());

    let rows: Vec<Row> = app
        .strategies
        .iter()
        .enumerate()
        .map(|(i, s)| {
            let is_selected = i == app.strategy_selected;
            let row_style = if is_selected {
                theme::style_selected()
            } else {
                theme::style_data()
            };

            let type_color = match s.strategy_type.as_deref() {
                Some("portfolio") => theme::FG_RUNNING,
                _ => theme::FG_SECONDARY,
            };

            Row::new(vec![
                Cell::from(s.name.clone()).style(Style::default().fg(theme::FG_HINT)),
                Cell::from(s.strategy_type.as_deref().unwrap_or("single").to_string())
                    .style(Style::default().fg(type_color)),
                Cell::from(s.strategy_class.clone().unwrap_or_default()),
            ])
            .style(row_style)
        })
        .collect();

    let widths = [
        Constraint::Min(14),
        Constraint::Length(10),
        Constraint::Min(14),
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(border_style)
                .title(Span::styled(title, theme::style_header())),
        );

    f.render_widget(table, area);
}

fn render_detail(f: &mut Frame, area: Rect, app: &App) {
    let is_focused = app.panel_focus == PanelFocus::Right;
    let border_style = if is_focused {
        theme::style_border_focused()
    } else {
        theme::style_border()
    };

    let strat = match app.strategies.get(app.strategy_selected) {
        Some(s) => s,
        None => {
            let p = Paragraph::new(Span::styled(
                "  No strategy selected",
                theme::style_dim(),
            ))
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(border_style)
                    .title(Span::styled(" DETAIL ", theme::style_header())),
            );
            f.render_widget(p, area);
            return;
        }
    };

    let title = format!(" DETAIL: {} ", &strat.name);

    let mut lines = vec![
        Line::from(""),
        kv_line("  Name   ", &strat.name),
        kv_line(
            "  Type   ",
            strat.strategy_type.as_deref().unwrap_or("single"),
        ),
        kv_line(
            "  Class  ",
            strat.strategy_class.as_deref().unwrap_or("-"),
        ),
        kv_line(
            "  Config ",
            strat.config_class.as_deref().unwrap_or("-"),
        ),
        kv_line(
            "  File   ",
            strat
                .file_path
                .as_deref()
                .and_then(|p| p.rsplit('/').next())
                .unwrap_or("-"),
        ),
    ];

    // Symbols
    if let Some(ref symbols) = strat.symbols {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  SYMBOLS",
            theme::style_header(),
        )));
        for sym in symbols {
            lines.push(Line::from(vec![
                Span::raw("    "),
                Span::styled(sym, Style::default().fg(theme::FG_HINT)),
            ]));
        }
    }

    // Actors
    if let Some(ref actors) = strat.actors {
        if !actors.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  ACTORS",
                theme::style_header(),
            )));
            for actor in actors {
                lines.push(Line::from(vec![
                    Span::raw("    "),
                    Span::styled(actor, Style::default().fg(theme::FG_SECONDARY)),
                ]));
            }
        }
    }

    // Versions
    if let Some(ref versions) = strat.versions {
        if !versions.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "  VERSIONS",
                theme::style_header(),
            )));
            for v in versions.iter().take(5) {
                let hash = v.code_hash.as_deref().unwrap_or("-").get(..8).unwrap_or("-");
                let date = v
                    .created_at
                    .as_deref()
                    .unwrap_or("-")
                    .get(..10)
                    .unwrap_or("-");
                lines.push(Line::from(vec![
                    Span::styled(format!("    v{} ", v.version), Style::default().fg(theme::FG_PRIMARY)),
                    Span::styled(hash, Style::default().fg(theme::FG_DIM)),
                    Span::raw("  "),
                    Span::styled(date, Style::default().fg(theme::FG_DIM)),
                ]));
            }
        }
    }

    let p = Paragraph::new(lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(border_style)
            .title(Span::styled(title, theme::style_header())),
    );
    f.render_widget(p, area);
}

fn kv_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(label.to_string(), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        Span::styled(value.to_string(), Style::default().fg(theme::FG_PRIMARY)),
    ])
}
