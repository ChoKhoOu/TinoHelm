pub mod backtest_detail;
pub mod backtest_form;
pub mod backtest_list;
pub mod node_status;
pub mod strategy_list;

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use crate::tui::app::{App, View, WsState};

/// Render the active view into the frame.
pub fn render(f: &mut Frame, app: &App) {
    let size = f.area();

    // Layout: [tab bar (1)] [main content (fill)] [hint bar (1)] [error banner? (1)]
    let has_error = app.error_banner.is_some();
    let constraints = if has_error {
        vec![
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
            Constraint::Length(1),
        ]
    } else {
        vec![
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
        ]
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(size);

    // Tab bar
    render_tabs(f, chunks[0], app);

    // Main content
    match app.current_view {
        View::BacktestList => backtest_list::render(f, chunks[1], app),
        View::BacktestDetail => backtest_detail::render(f, chunks[1], app),
        View::BacktestForm => backtest_form::render(f, chunks[1], app),
        View::StrategyList => strategy_list::render(f, chunks[1], app),
        View::NodeStatus => node_status::render(f, chunks[1], app),
    }

    // Key hint bar
    render_hints(f, chunks[2], app);

    // Error banner (if present)
    if has_error {
        render_error(f, chunks[3], app);
    }
}

fn render_tabs(f: &mut Frame, area: Rect, app: &App) {
    let tabs = [
        ("1", "Backtests", View::BacktestList),
        ("2", "Strategies", View::StrategyList),
        ("3", "Nodes", View::NodeStatus),
    ];

    let mut spans = Vec::new();
    spans.push(Span::raw(" "));

    for (key, label, view) in &tabs {
        let style = if app.current_view == *view
            || (app.current_view == View::BacktestDetail && *view == View::BacktestList)
            || (app.current_view == View::BacktestForm && *view == View::BacktestList)
        {
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::DarkGray)
        };

        spans.push(Span::styled(format!("[{}]", key), Style::default().fg(Color::DarkGray)));
        spans.push(Span::styled(format!(" {} ", label), style));
        spans.push(Span::raw("  "));
    }

    // WS connection indicator (right-aligned)
    let (ws_dot, ws_color) = match app.ws_state {
        WsState::Connected => ("●", Color::Green),
        WsState::Connecting => ("◐", Color::Yellow),
        WsState::Disconnected => ("○", Color::Red),
    };
    spans.push(Span::raw("  "));
    spans.push(Span::styled(ws_dot, Style::default().fg(ws_color)));
    spans.push(Span::raw(" WS "));

    let line = Line::from(spans);
    f.render_widget(Paragraph::new(line), area);
}

fn render_hints(f: &mut Frame, area: Rect, app: &App) {
    let view_hints = match app.current_view {
        View::BacktestList => backtest_list::hints(),
        View::BacktestDetail => backtest_detail::hints(),
        View::BacktestForm => backtest_form::hints(),
        View::StrategyList => strategy_list::hints(),
        View::NodeStatus => node_status::hints(),
    };

    let mut spans = Vec::new();
    spans.push(Span::raw(" "));

    for (key, desc) in view_hints {
        spans.push(Span::styled(key, Style::default().fg(Color::Yellow)));
        spans.push(Span::raw(format!(" {} ", desc)));
        spans.push(Span::styled("│", Style::default().fg(Color::DarkGray)));
        spans.push(Span::raw(" "));
    }

    // Global hints
    spans.push(Span::styled("1/2/3", Style::default().fg(Color::Yellow)));
    spans.push(Span::raw(" tab "));
    spans.push(Span::styled("│", Style::default().fg(Color::DarkGray)));
    spans.push(Span::raw(" "));
    spans.push(Span::styled("q", Style::default().fg(Color::Yellow)));
    spans.push(Span::raw(" quit"));

    let line = Line::from(spans);
    f.render_widget(Paragraph::new(line), area);
}

fn render_error(f: &mut Frame, area: Rect, app: &App) {
    if let Some(ref msg) = app.error_banner {
        let line = Line::from(vec![
            Span::styled(" ERROR ", Style::default().fg(Color::White).bg(Color::Red)),
            Span::raw(" "),
            Span::styled(msg.as_str(), Style::default().fg(Color::Red)),
        ]);
        f.render_widget(Paragraph::new(line), area);
    }
}
