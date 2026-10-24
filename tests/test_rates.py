import pytest
from drc_sync.rates import apply_website_rate, rate_config


@pytest.mark.parametrize('mode,percent,expected', [('discount','10','4500.00'),('increase','10','5500.00'),('discount','0','5000'),('increase','0.5','5025.00')])
def test_price_adjustment_and_original_preserved(mode,percent,expected):
    original={'diamonds':[{'PricePerCarat':'5000','StockId':'A','Discount':'20','Final Price':'7000'}]}
    result=apply_website_rate(original,{'drc':{'mode':mode,'percent':percent}})
    assert result['diamonds'][0]['PricePerCarat']==expected
    assert original['diamonds'][0]['PricePerCarat']=='5000'
    assert result['diamonds'][0]['Discount']=='20'
    assert result['diamonds'][0]['Final Price']=='7000'
    assert apply_website_rate(original,{'drc':{'mode':mode,'percent':percent}},'another-site')==original


def test_rounding_and_numeric_type():
    result=apply_website_rate({'diamonds':[{'PricePerCarat':1}]},{'drc':{'mode':'increase','percent':'0.5'}})
    assert result['diamonds'][0]['PricePerCarat']==1.01


@pytest.mark.parametrize('mode,percent',[('discount','100'),('discount','-1'),('increase','NaN'),('increase','Infinity'),('increase',True),('increase','100.01'),('other','5')])
def test_invalid_settings_rejected(mode,percent):
    with pytest.raises(ValueError): rate_config({'drc':{'mode':mode,'percent':percent}})


@pytest.mark.parametrize('price',[None,'bad','0','-1','NaN',True,'0.01'])
def test_invalid_adjusted_prices_blocked(price):
    with pytest.raises(ValueError): apply_website_rate({'diamonds':[{'PricePerCarat':price}]},{'drc':{'mode':'discount','percent':'99.99'}})

@pytest.mark.parametrize('mode,percent,expected',[('increase','100','10000.00'),('increase','0','5000'),('discount','99.99','0.50')])
def test_new_percentage_boundaries(mode,percent,expected):
    assert apply_website_rate({'diamonds':[{'PricePerCarat':'5000'}]},{'drc':{'mode':mode,'percent':percent}})['diamonds'][0]['PricePerCarat']==expected
