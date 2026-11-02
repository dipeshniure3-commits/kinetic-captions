"""Word lists used by kc.style to score emphasis. All entries lowercase."""

# Function words: rendered small, never the hero of a phrase.
STOP_WORDS = frozenset("""
a an the and or but nor so yet for of to in on at by with from into onto upon
over under about as than then that this these those there here it its it's
is am are was were be been being do does did done have has had having will
would shall should can could may might must i me my mine we us our you your
yours he him his she her hers they them their what which who whom whose when
where why how just also very really too if because while until up down out off
again once all any both each few more most other some such own same only
s t don't i'm you're we're they're i've you've i'll you'll it'll gonna wanna
got get gets oh uh um like yeah okay ok well let let's
""".split())

# Tier 1: the absolute "money words" of motivational speeches. Big, slam.
POWER_TIER1 = frozenset("""
never nothing everything everyone nobody anything always forever death die
dead dying life alive greatest impossible believe dream dreams fear pain
win winning won lose losing lost success fail failure failed sacrifice
legacy destiny god love hate war fight truth free freedom power champion
excuses excuse perfect become exist regret unstoppable meant
""".split())

# Tier 2: strong content words that deserve a size bump.
POWER_TIER2 = frozenset("""
promised promise appreciate moment moments time today tomorrow now future
goal goals hustle grind work hard harder hardest discipline focus purpose
passion faith hope heart soul mind mindset strong stronger strongest weak
great best better amazing incredible greatness rich money wealth broke poor
million billion king queen world mountain fire storm chance opportunity
everyday first last alone family respect pride proud courage
brave hungry ready rise fall push pushed quit stop start keep keeps change
grow growth learn earn deserve worth victory defeat survive struggle battle
waiting wait further person yourself action begin move
legend history beginning end finish limit limits greater better reality
""".split())

# Negative / dark words: red accent in colour presets, shake candidates.
NEGATIVE = frozenset("""
no not never nothing nobody none neither nor cannot can't won't don't
didn't doesn't isn't wasn't aren't weren't shouldn't wouldn't couldn't
death die dead dying kill killed pain fear afraid scared fail failure failed
lose losing lost loss quit broke poor hate wrong worst weak alone dark
stop except without impossible never-ending enemy hell suffer suffering
""".split())

# Intensifiers boost the following content word a little.
INTENSIFIERS = frozenset("""
every single all entire whole absolutely totally completely truly most
extremely super so really very
""".split())

NUMBER_WORDS = frozenset("""
zero one two three four five six seven eight nine ten eleven twelve
thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty
forty fifty sixty seventy eighty ninety hundred thousand million billion
trillion first second third half double triple percent
""".split())

QUESTION_WORDS = frozenset("what why how who where when".split())

# Words that typically open a new clause: good places to start a new phrase.
CLAUSE_STARTERS = frozenset("""
that because and but which who so if when while until unless though although
cause 'cause or then
""".split())

# Contractions/words that negate the next content word ("doesn't EXIST").
NEGATORS = frozenset("""
not never no don't doesn't didn't won't can't cannot isn't aren't wasn't
weren't shouldn't wouldn't couldn't ain't nothing
""".split())
