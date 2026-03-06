# Backtest Optimization

## Purpose

Provides Optuna-based hyperparameter optimization for trading strategies, enabling automated search for optimal strategy parameters with configurable fitness objectives and train/test data splitting to prevent overfitting.

## Requirements

### Requirement: Optuna study execution
The system SHALL run Optuna optimization studies that evaluate strategy parameters by executing backtests and measuring fitness objectives.

#### Scenario: Run optimization with default settings
- **WHEN** an optimization is started with strategy, symbol, interval, date range, and n_trials
- **THEN** system creates an Optuna study with TPE sampler, runs n_trials backtest iterations each with different parameter combinations, and records trial results

#### Scenario: Strategy declares optimizable parameters
- **WHEN** a strategy's config class has numeric fields with metadata annotations (min, max, step)
- **THEN** optimizer extracts parameter ranges from the config schema and uses them as Optuna search dimensions

#### Scenario: Strategy has no optimizable parameters
- **WHEN** a strategy's config class has no annotated numeric fields
- **THEN** system returns an error indicating the strategy has no parameters to optimize

### Requirement: Fitness objective selection
The system SHALL support multiple fitness objectives for optimization.

#### Scenario: Optimize for Sharpe Ratio
- **WHEN** optimization is started with fitness_objective="sharpe"
- **THEN** each trial's fitness value is the Sharpe Ratio extracted from the backtest result statistics

#### Scenario: Optimize for Calmar Ratio
- **WHEN** optimization is started with fitness_objective="calmar"
- **THEN** each trial's fitness value is the Calmar Ratio (annual_return / max_drawdown)

#### Scenario: Optimize for Sortino Ratio
- **WHEN** optimization is started with fitness_objective="sortino"
- **THEN** each trial's fitness value is the Sortino Ratio from backtest result statistics

#### Scenario: Optimize for Net Profit
- **WHEN** optimization is started with fitness_objective="profit"
- **THEN** each trial's fitness value is the total_pnl from backtest result statistics

### Requirement: Train/test split for overfitting prevention
The system SHALL support splitting the date range into training and testing periods.

#### Scenario: Default train/test split
- **WHEN** optimization is started without specifying train_pct
- **THEN** system uses 85% of the date range for training (parameter search) and 15% for testing (validation)

#### Scenario: Custom train/test split
- **WHEN** optimization is started with train_pct=70
- **THEN** system uses 70% of the date range for training and 30% for testing

#### Scenario: Validation backtest on best params
- **WHEN** optimization completes and best parameters are found
- **THEN** system automatically runs a validation backtest using the best parameters on the test period and includes test_metrics in the optimization result

### Requirement: Optimization result storage
The system SHALL persist optimization study results in the database.

#### Scenario: Store optimization run
- **WHEN** an optimization study is created
- **THEN** system creates an `optimization_runs` record with: id, strategy_id, symbol, interval, start_date, end_date, n_trials, fitness_objective, train_pct, status, best_params_json, best_value, created_at, completed_at

#### Scenario: Store trial results
- **WHEN** each trial completes within an optimization study
- **THEN** system updates the optimization_runs record with current progress (trials_completed) and best result so far

#### Scenario: Optimization completes
- **WHEN** all n_trials have been evaluated
- **THEN** system updates the optimization_runs record with status "completed", final best_params, best_value, and full trial history in an artifact file

### Requirement: Optimization progress reporting
The system SHALL report optimization progress in real-time.

#### Scenario: Progress via status endpoint
- **WHEN** client polls `GET /api/backtest/optimize/{id}/status` during an active optimization
- **THEN** system returns trials_completed, total_trials, current best_params, and current best_value

#### Scenario: Progress via WebSocket
- **WHEN** an optimization trial completes
- **THEN** system publishes an event via EventBridge with type `optimization.trial_complete` containing `{optimization_id, trial_number, params, value, best_so_far}`
