import pandas as pd


def build_positions(margin: pd.DataFrame, securities: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Build financing-purchase batches with adjusted market-average entry-price proxies."""
    keys = securities[['exchange', 'security_code']].drop_duplicates()
    positions = margin.merge(keys, on=['exchange', 'security_code'], how='inner').merge(
        prices,
        on=['trade_date', 'exchange', 'security_code'],
        how='inner',
        validate='one_to_one',
    )
    positions = positions.loc[
        (positions.financing_buy_amount > 0) & (positions.volume > 0) & (positions.close_unadjusted > 0)
    ].copy()
    positions['entry_price'] = positions.amount / positions.volume * positions.close / positions.close_unadjusted
    return positions


def calculate_sample_coverage(margin: pd.DataFrame, sample: pd.DataFrame) -> pd.DataFrame:
    """Calculate fixed-sample financing purchases as a share of the full market."""
    sample_keys = sample[['exchange', 'security_code']].drop_duplicates()
    all_daily = margin.groupby('trade_date', as_index=False).financing_buy_amount.sum()
    sample_daily = (
        margin.merge(sample_keys, on=['exchange', 'security_code'], how='inner')
        .groupby('trade_date', as_index=False)
        .agg(sample_margin_purchase=('financing_buy_amount', 'sum'), sample_active_count=('security_code', 'nunique'))
    )
    coverage = all_daily.rename(columns={'financing_buy_amount': 'all_margin_purchase'}).merge(
        sample_daily, on='trade_date', how='left'
    )
    coverage = coverage.fillna({'sample_margin_purchase': 0, 'sample_active_count': 0})
    coverage['sample_coverage'] = coverage.sample_margin_purchase / coverage.all_margin_purchase
    _assert_ratio(coverage, 'sample_coverage')
    return coverage


def calculate_cumulative_profitability(positions: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate the cumulative positive-return financing-amount share proxy by evaluation date."""
    records = []
    for evaluation_date, evaluation_prices in prices.groupby('trade_date', sort=True):
        current = _evaluate_positions(positions, evaluation_date, evaluation_prices)
        if current.empty:
            continue
        current['profitable'] = current.security_return.gt(0)
        total_amount = current.financing_buy_amount.sum()
        records.append(
            {
                'evaluation_date': evaluation_date,
                'cumulative_entry_amount': total_amount,
                'profitable_amount': current.loc[current.profitable, 'financing_buy_amount'].sum(),
                'profit_ratio': current.loc[current.profitable, 'financing_buy_amount'].sum() / total_amount,
                'profitable_batch_count': current.profitable.sum(),
                'total_batch_count': len(current),
            }
        )
    result = pd.DataFrame(records)
    _assert_ratio(result, 'profit_ratio')
    return result


def prepare_security_metadata(
    securities: pd.DataFrame, industries: pd.DataFrame, basics: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Attach security types and normalized industry labels to a security universe."""
    columns = ['exchange', 'security_code', 'security_type']
    if 'security_type' in securities:
        metadata = securities[columns].drop_duplicates()
    elif basics is not None:
        metadata = securities[['exchange', 'security_code']].drop_duplicates().merge(
            basics[columns], on=['exchange', 'security_code'], how='left', validate='one_to_one'
        )
    else:
        raise ValueError('security_type is required in securities or basics.')
    metadata = metadata.merge(
        industries[['exchange', 'security_code', 'industry', 'industry_classification', 'industry_update_date']],
        on=['exchange', 'security_code'],
        how='left',
        validate='one_to_one',
    )
    metadata['industry'] = metadata.industry.replace('', pd.NA)
    metadata['industry_classification'] = metadata.industry_classification.replace('', pd.NA)
    metadata.loc[metadata.security_type.ne('stock'), 'industry'] = '非股票证券'
    metadata.loc[metadata.security_type.eq('stock') & metadata.industry.isna(), 'industry'] = '未分类'
    return metadata


def calculate_tier_profitability(
    margin: pd.DataFrame,
    tiers: pd.DataFrame,
    tier_prices: pd.DataFrame,
    tier_metadata: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate cumulative return proxies for first-day financing-amount groups."""
    positions = build_positions(margin, tiers, tier_prices).merge(
        tiers[['tier', 'first_day_rank', 'exchange', 'security_code']],
        on=['exchange', 'security_code'],
        how='inner',
        validate='many_to_one',
    )
    records = []
    for evaluation_date, evaluation_prices in tier_prices.groupby('trade_date', sort=True):
        current = _evaluate_positions(positions, evaluation_date, evaluation_prices)
        if current.empty:
            continue
        current['profitable_amount'] = current.financing_buy_amount.where(current.security_return.gt(0), 0)
        daily = current.groupby(['tier', 'exchange', 'security_code'], as_index=False).agg(
            security_name=('security_name', 'last'),
            cumulative_margin_purchase=('financing_buy_amount', 'sum'),
            profitable_amount=('profitable_amount', 'sum'),
        )
        daily['evaluation_date'] = evaluation_date
        records.append(daily)
    result = pd.concat(records, ignore_index=True).merge(
        tier_metadata[
            [
                'tier',
                'exchange',
                'security_code',
                'security_type',
                'industry',
                'industry_classification',
                'first_day_rank',
                'financing_buy_amount',
            ]
        ],
        on=['tier', 'exchange', 'security_code'],
        how='left',
        validate='many_to_one',
    ).rename(columns={'financing_buy_amount': 'first_day_financing_buy_amount'})
    result['cumulative_profit_ratio'] = result.profitable_amount / result.cumulative_margin_purchase
    _assert_ratio(result, 'cumulative_profit_ratio')
    return result


def calculate_industry_cumulative_profitability(
    positions: pd.DataFrame,
    prices: pd.DataFrame,
    sample_metadata: pd.DataFrame,
    tier_metadata: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate cumulative return proxies for fixed-sample stocks in group industries."""
    industry_scope = (
        tier_metadata.loc[tier_metadata.security_type.eq('stock'), ['industry', 'industry_classification']]
        .dropna()
        .drop_duplicates()
        .sort_values(['industry_classification', 'industry'])
        .reset_index(drop=True)
    )
    stocks = sample_metadata.loc[sample_metadata.security_type.eq('stock')].merge(
        industry_scope,
        on=['industry', 'industry_classification'],
        how='inner',
        validate='many_to_one',
    )
    industry_positions = positions.merge(
        stocks[['exchange', 'security_code', 'industry', 'industry_classification']],
        on=['exchange', 'security_code'],
        how='inner',
        validate='many_to_one',
    )
    records = []
    for evaluation_date, evaluation_prices in prices.groupby('trade_date', sort=True):
        current = _evaluate_positions(industry_positions, evaluation_date, evaluation_prices)
        if current.empty:
            continue
        current['profitable_amount'] = current.financing_buy_amount.where(current.security_return.gt(0), 0)
        daily = current.groupby(['industry', 'industry_classification'], as_index=False).agg(
            cumulative_margin_purchase=('financing_buy_amount', 'sum'),
            profitable_amount=('profitable_amount', 'sum'),
            stock_count=('security_code', 'nunique'),
        )
        daily['evaluation_date'] = evaluation_date
        records.append(daily)
    result = pd.concat(records, ignore_index=True)
    result['cumulative_profit_ratio'] = result.profitable_amount / result.cumulative_margin_purchase
    _assert_ratio(result, 'cumulative_profit_ratio')
    summary = (
        stocks.groupby(['industry', 'industry_classification'], as_index=False)
        .agg(样本股票数量=('security_code', 'nunique'))
        .sort_values(['industry_classification', 'industry'])
    )
    return result, summary


def calculate_rolling_profitability(
    positions: pd.DataFrame, prices: pd.DataFrame, window_days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate security-level and aggregate rolling return proxies."""
    evaluation_dates = prices.trade_date.drop_duplicates().sort_values().tolist()
    security_records = []
    overall_records = []
    for date_index, evaluation_date in enumerate(evaluation_dates):
        window_start_date = evaluation_dates[max(0, date_index - window_days + 1)]
        current = _evaluate_positions(
            positions.loc[positions.trade_date.between(window_start_date, evaluation_date)],
            evaluation_date,
            prices.loc[prices.trade_date.eq(evaluation_date)],
        )
        if current.empty:
            continue
        current['profitable_amount'] = current.financing_buy_amount.where(current.security_return.gt(0), 0)
        current['weighted_return_amount'] = current.financing_buy_amount * current.security_return
        trading_days = min(date_index + 1, window_days)
        security_daily = current.groupby(['exchange', 'security_code'], as_index=False).agg(
            security_name=('security_name', 'last'),
            window_margin_purchase=('financing_buy_amount', 'sum'),
            profitable_amount=('profitable_amount', 'sum'),
            weighted_return_amount=('weighted_return_amount', 'sum'),
        )
        security_daily['rolling_profit_ratio'] = security_daily.profitable_amount / security_daily.window_margin_purchase
        security_daily['rolling_weighted_return'] = (
            security_daily.weighted_return_amount / security_daily.window_margin_purchase
        )
        security_daily['evaluation_date'] = evaluation_date
        security_daily['window_start_date'] = window_start_date
        security_daily['window_trading_days'] = trading_days
        security_daily['is_full_window'] = trading_days == window_days
        security_records.append(
            security_daily[
                [
                    'evaluation_date',
                    'exchange',
                    'security_code',
                    'security_name',
                    'window_start_date',
                    'window_trading_days',
                    'window_margin_purchase',
                    'profitable_amount',
                    'rolling_profit_ratio',
                    'rolling_weighted_return',
                    'is_full_window',
                ]
            ]
        )
        total_amount = current.financing_buy_amount.sum()
        overall_records.append(
            {
                'evaluation_date': evaluation_date,
                'window_start_date': window_start_date,
                'window_trading_days': trading_days,
                'window_margin_purchase': total_amount,
                'profitable_amount': current.profitable_amount.sum(),
                'rolling_profit_ratio': current.profitable_amount.sum() / total_amount,
                'is_full_window': trading_days == window_days,
            }
        )
    security = pd.concat(security_records, ignore_index=True)
    overall = pd.DataFrame(overall_records)
    _assert_ratio(security, 'rolling_profit_ratio')
    _assert_ratio(overall, 'rolling_profit_ratio')
    return security, overall


def select_rolling_securities(
    rolling_security: pd.DataFrame,
    security_metadata: pd.DataFrame,
    excluded_security_codes: set[str],
    size: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Rank rolling results after symmetric exclusions and select exploratory high/low groups."""
    ranking = (
        rolling_security.loc[rolling_security.is_full_window]
        .groupby(['exchange', 'security_code'], as_index=False)
        .agg(
            security_name=('security_name', 'last'),
            mean_profit_ratio=('rolling_profit_ratio', 'mean'),
            mean_weighted_return=('rolling_weighted_return', 'mean'),
        )
        .merge(security_metadata, on=['exchange', 'security_code'], how='left', validate='one_to_one')
    )
    if size <= 0:
        raise ValueError('Selection size must be positive.')
    ranking = ranking.loc[~ranking.security_code.isin(excluded_security_codes)].copy()
    if len(ranking) < size * 2:
        raise RuntimeError(f'At least {size * 2} eligible securities are required for disjoint high/low groups.')
    ranking['profit_ratio_rank'] = ranking.mean_profit_ratio.rank(ascending=False, method='min').astype(int)
    ratio_high = ranking.nlargest(size, 'mean_profit_ratio')
    ratio_low = ranking.nsmallest(size, 'mean_profit_ratio')
    return_high = ranking.loc[ranking.mean_weighted_return.gt(0)].nlargest(size, 'mean_profit_ratio')
    return_low = ranking.loc[ranking.mean_weighted_return.lt(0)].nsmallest(size, 'mean_profit_ratio')
    if len(return_high) != size or len(return_low) != size:
        raise RuntimeError(f'金额加权收益方向筛选后的证券数量不足 {size} 只。')
    ratio_keys = pd.concat([ratio_high, ratio_low])[['exchange', 'security_code']]
    return_keys = pd.concat([return_high, return_low])[['exchange', 'security_code']]
    ranking_index = ranking.set_index(['exchange', 'security_code']).index
    ranking['selected_profit_ratio_only'] = ranking_index.isin(ratio_keys.set_index(['exchange', 'security_code']).index)
    ranking['selected_with_return_filter'] = ranking_index.isin(return_keys.set_index(['exchange', 'security_code']).index)
    ranking['selected_group'] = pd.NA
    ranking.loc[ranking_index.isin(return_high.set_index(['exchange', 'security_code']).index), 'selected_group'] = 'high'
    ranking.loc[ranking_index.isin(return_low.set_index(['exchange', 'security_code']).index), 'selected_group'] = 'low'
    selection = ranking[
        [
            'exchange', 'security_code', 'security_name', 'security_type', 'industry', 'industry_classification',
            'industry_update_date', 'mean_profit_ratio', 'mean_weighted_return', 'profit_ratio_rank',
            'selected_profit_ratio_only', 'selected_with_return_filter', 'selected_group',
        ]
    ].sort_values(['profit_ratio_rank', 'security_code'])
    return selection, ratio_high, ratio_low, return_high, return_low


def calculate_rolling_industry_profitability(
    rolling_security: pd.DataFrame, selection: pd.DataFrame, security_metadata: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Aggregate rolling return proxies for the stock industries represented by selected securities."""
    scope = (
        selection.loc[selection.selected_group.notna() & selection.security_type.eq('stock'), ['industry', 'industry_classification']]
        .drop_duplicates()
        .sort_values(['industry_classification', 'industry'])
        .reset_index(drop=True)
    )
    stocks = security_metadata.loc[security_metadata.security_type.eq('stock')].merge(
        scope, on=['industry', 'industry_classification'], how='inner', validate='many_to_one'
    )
    result = rolling_security.merge(
        stocks[['exchange', 'security_code', 'industry', 'industry_classification']],
        on=['exchange', 'security_code'],
        how='inner',
        validate='many_to_one',
    )
    result = result.groupby(['evaluation_date', 'industry', 'industry_classification'], as_index=False).agg(
        window_start_date=('window_start_date', 'first'),
        window_trading_days=('window_trading_days', 'first'),
        window_margin_purchase=('window_margin_purchase', 'sum'),
        profitable_amount=('profitable_amount', 'sum'),
        stock_count=('security_code', 'nunique'),
        is_full_window=('is_full_window', 'first'),
    )
    result['rolling_profit_ratio'] = result.profitable_amount / result.window_margin_purchase
    _assert_ratio(result, 'rolling_profit_ratio')
    summary = (
        stocks.groupby(['industry', 'industry_classification'], as_index=False)
        .agg(样本股票数量=('security_code', 'nunique'))
        .sort_values(['industry_classification', 'industry'])
    )
    return result, scope, summary


def compare_rolling_selection(
    ranking: pd.DataFrame,
    ratio_high: pd.DataFrame,
    ratio_low: pd.DataFrame,
    return_high: pd.DataFrame,
    return_low: pd.DataFrame,
) -> pd.DataFrame:
    """Show overlap, additions, and removals between the two rolling-selection rules."""
    records = []
    for group, ratio_only, return_filtered in [
        ('high', ratio_high, return_high),
        ('low', ratio_low, return_low),
    ]:
        ratio_keys = set(ratio_only[['exchange', 'security_code']].itertuples(index=False, name=None))
        return_keys = set(return_filtered[['exchange', 'security_code']].itertuples(index=False, name=None))
        for status, keys in [
            ('重合', ratio_keys & return_keys),
            ('新增', return_keys - ratio_keys),
            ('剔除', ratio_keys - return_keys),
        ]:
            records.extend(
                {'分组': group, '状态': status, 'exchange': exchange, 'security_code': code}
                for exchange, code in sorted(keys)
            )
    return pd.DataFrame(records).merge(
        ranking[['exchange', 'security_code', 'security_name', 'security_type', 'industry', 'industry_classification']],
        on=['exchange', 'security_code'],
        how='left',
        validate='many_to_one',
    )


def _evaluate_positions(positions: pd.DataFrame, evaluation_date: pd.Timestamp, prices: pd.DataFrame) -> pd.DataFrame:
    current = positions.loc[positions.trade_date <= evaluation_date].merge(
        prices[['exchange', 'security_code', 'close']].rename(columns={'close': 'close_evaluation'}),
        on=['exchange', 'security_code'],
        how='inner',
        validate='many_to_one',
    )
    current['security_return'] = current.close_evaluation / current.entry_price - 1
    return current


def _assert_ratio(frame: pd.DataFrame, column: str) -> None:
    if not frame[column].between(0, 1).all():
        raise ValueError(f'{column} must be between 0 and 1.')
