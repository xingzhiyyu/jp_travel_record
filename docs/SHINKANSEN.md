# 新干线镜头分类

实现：`travel_record/shinkansen.py`。只决定是否免除普通长途铁路的缩放下限，
不修改行程名称、路线几何、日夜模式或换乘缩放时长；不自动新增 OSM relation。

## 覆盖写法

- 通用标记：新幹線、新干线及混合简繁字形、しんかんせん／シンカンセン、
  Shinkansen、Shin-kan-sen、Bullet Train；忽略英文大小写、全半角和罗马字长音。
  因此东海道、山阳、东北、北海道、上越、北陆／长野、九州／西九州、山形和秋田等线路
  不需要逐一硬编码。识别名称不代表线路已运营或已有可用几何。
- 列车名：Nozomi、Hikari、Kodama、Mizuho、Sakura、Tsubame、Kamome、Hayabusa、
  Hayate、Yamabiko、Nasuno、Komachi、Tsubasa、Toki、Tanigawa、Kagayaki、Hakutaka、
  Asama、Tsurugi；附相应日文假名和常见中文译名。
- 历史名称：Aoba／青葉、Asahi／朝日；Max 前缀、Super Komachi、Hikari Rail Star、
  Toreiyu Tsubasa 也可识别。历史名称识别不代表恢复历史线路的寻路功能。
- 允许车次号、号／號、JR 前缀及明确的组合，例如 `のぞみ１２３号`、`Nozomi No. 12`、
  `Max とき321号`、`はやぶさ・こまち`。

优先参考已解析 `details.osm_name`、`osm_name_en`、`canonical_line`，旧解析对象缺少新增字段
也可使用名称规则。非铁路模式不应用新干线分类。普通东海道本线、山阳本线、北陆本线不会因区域名被误判。

## 歧义与排除

Relay／リレー／接力／接驳、寝台／卧铺／Sleeper、明确“在来线”等修饰语排除，
避免把接力海鸥或寝台樱号当成新干线。`Sakura Tram`、`ときわ` 等不做子字符串误匹配。

只写 `さくら`、`かもめ` 等裸列车名且没有更明确解析信息时，按其新干线用途分类。
这些名字历史上可能被在来线复用：历史输入应注明“寝台”“在来线”，或者使用明确基础设施线路名。
不以 N700、E5 等车辆型号、运营商名称或任意“高速／高铁”字样推断新干线。
名称表可扩展，但不能承诺自动识别所有未来昵称、错别字或历史歧义。

## 核对来源

- JR 东海 Nozomi / Hikari / Kodama：https://global.jr-central.co.jp/en/nozomi/
- JR 东日本东北系：https://www.jreast.co.jp/en/multi/routemaps/tohokushinkansen.html
- JR 东日本北陆／上越系：https://www.jreast.co.jp/en/train/shinkan/e7.html
- JR 西日本北陆系：https://www.jr-odekake.net/railroad/shinkansen/kagayaki/
- JR 九州新干线列车名：https://www.jrkyushu.co.jp/english/railpass/railpass.html
- JR 九州 Kamome 与 Relay Kamome 区别：https://www.jrkyushu.co.jp/english/train/shinkansen_kamome.html
- JR 北海道 Hayabusa / Hayate：https://www.jrhokkaido.co.jp/train/shinkansen.html
- JR 东日本历史 Max 列车：https://www.jreast.co.jp/press/2018/20190109.pdf

测试：`python -m unittest discover -s tests -v`，包含所有别名、各语种线路标记、车次、
反例、已解析名称兜底及真实 `_target_zooms` 的中英日一致性测试；不下载底图或编码整片。
