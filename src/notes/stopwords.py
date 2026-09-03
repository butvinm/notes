"""A small built-in stopword list for FTS queries.

`textnorm.fts_query` drops these after normalization, so the Russian entries are lemmas
(`pymorphy3` maps `этого` to `это`, `было` to `быть`, `нас` to `мы` before the check)
plus a few raw forms that the lemmatizer leaves alone.
English entries include the stems that `tokens` leaves behind from contractions (`doesn`, `ll`, `ve`),
because the apostrophe splits `doesn't` into `doesn` and `t`.
The list is deliberately short: it only removes words that never help a search, not every function word.

The words are kept as whitespace-separated text rather than the list literal ruff's SIM905 asks for:
one entry per line would turn a list meant to be scanned and edited by eye into a two-hundred-line wall.
"""

ENGLISH = frozenset(
    """
    a an the and or but if of to in on at by for with from as into over under about after before
    is are was were be been being am
    it its this that these those there here
    i me my we our us you your he him his she her they them their
    what which who whom why how when where
    do does did doing done have has had having
    not no nor so than then too very just also only own same such
    can could should would will shall may might must
    some any all more most other another each every
    up down out off again further
    let please
    doesn don didn isn aren wasn weren hasn haven hadn won wouldn couldn shouldn ll ve re
    """.split()  # noqa: SIM905
)

RUSSIAN = frozenset(
    """
    и в во не что он она оно они на я мы ты вы с со как а то это этот всё все весь так его её ее их
    но да к у же за бы по только вот от ещё еще нет о об из ему теперь когда даже ну ли если уже или ни
    быть был была было были до вас ведь там потом себя ничего может мочь тут где есть надо для чем
    сам чтоб чтобы без будто чего раз тоже себе под будет тогда кто того потому какой совсем здесь
    один почти мой тем сейчас куда зачем никогда можно при наконец другой хоть после над больше тот
    через про всего много такой более всегда конечно между почему который свой наш ваш этом этой эти
    """.split()  # noqa: SIM905
)

STOPWORDS = ENGLISH | RUSSIAN
