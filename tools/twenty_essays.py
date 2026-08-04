"""Twenty fresh error-heavy essays for the user-level burn-in campaign.

Testing fixtures only: varied topics and error classes (agreement,
homophones, tense, run-ons, apostrophes, double negatives, articles,
comma splices, malapropisms, idioms, obligations, temporal connectors).
Corrected outputs are never saved as deliverables.
"""

TWENTY_ESSAYS = (
    {
        "id": "essay-01-coffee",
        "text": (
            "The coffee shop on the corner have been their for twenty years and it dont "
            "never seem to change. The owner say hello to every customer even when the "
            "line stretch out the door. Regulars orders the same drink every morning and "
            "the baristas remembers all of them without asking.\n"
            "Last winter the espresso machine breaked down during the morning rush. "
            "Nobody couldnt get there usual drinks and the whole neighborhood felt of. "
            "The repair guy said the machine needed two new part and it would of took a "
            "week to arrive."
        ),
        "forbidden": (
            "shop on the corner have", "been their for", "dont never seem",
            "The owner say", "line stretch", "Regulars orders", "baristas remembers",
            "machine breaked", "Nobody couldnt", "get there usual", "felt of",
            "two new part", "would of took",
        ),
    },
    {
        "id": "essay-02-roadtrip",
        "text": (
            "Me and my brother taked a road trip threw four states last summer. We was "
            "suppose to leave at dawn but the car wouldnt start untill almost noon. Are "
            "map app keeped loosing signal in the mountains so we misses two exits.\n"
            "By the time we reach the campground it were already dark. Neither of us "
            "were able to put up the tent proper and we slepted in the car. The next "
            "morning a ranger told us we had parked in a spot what was reserve for the "
            "host."
        ),
        "forbidden": (
            "Me and my brother taked", "trip threw four", "We was suppose",
            "wouldnt start untill", "Are map app", "keeped loosing", "we misses",
            "time we reach", "it were already", "Neither of us were",
            "tent proper", "we slepted", "spot what was reserve",
        ),
    },
    {
        "id": "essay-03-garden",
        "text": (
            "Gardening take more patience then most peoples expects. Seeds dont sprout "
            "over night no matter how much you stares at them. My neighbor she waters "
            "her tomatos twice a day and swear it make them sweeter.\n"
            "This spring the frost catched everybody of guard and killed alot of the "
            "early plantings. Us gardeners has to start over from seed, witch set the "
            "harvest back six week at least."
        ),
        "forbidden": (
            "Gardening take", "patience then most", "peoples expects",
            "over night no matter", "you stares", "neighbor she waters",
            "tomatos", "swear it make", "frost catched", "of guard",
            "alot of the", "Us gardeners has", "witch set", "six week at",
        ),
    },
    {
        "id": "essay-04-videogames",
        "text": (
            "Video games gets blamed for alot of things but they also teaches problem "
            "solving. A player which keep dying at the same boss have to change there "
            "strategy or quit. Most kids chooses to adapt, trying new weapons items and "
            "routes untill something work.\n"
            "My cousin beated a puzzle game last month that taked him three tries a day "
            "for two whole month. He said the ending were worth every failed attempt "
            "even tho his friends thinked he was wasting time."
        ),
        "forbidden": (
            "games gets blamed", "they also teaches", "which keep dying",
            "boss have to", "change there strategy", "kids chooses",
            "untill something work", "cousin beated", "taked him",
            "two whole month", "ending were worth", "even tho", "friends thinked",
        ),
    },
    {
        "id": "essay-05-weather",
        "text": (
            "The weather this year have been stranger then anyone remember. It snowed "
            "in april then hitted ninety degrees the very next week. Farmers dont know "
            "weather to plant early or wait and the almanac aint no help neither.\n"
            "Scientists says these swings is becoming more common. Storm systems moves "
            "slower now witch means rain sit over one town for days while the next "
            "county over stay bone dry."
        ),
        "forbidden": (
            "year have been", "stranger then anyone", "anyone remember",
            "snowed in april then hitted", "know weather to plant",
            "aint no help neither", "Scientists says", "swings is",
            "systems moves", "witch means", "rain sit", "county over stay",
        ),
    },
    {
        "id": "essay-06-cooking",
        "text": (
            "Learning to cook dont happen over night, it happen one burned dinner at a "
            "time. My first omelet losed its shape and become scrambled eggs before I "
            "could flip it. Recipes always says things like fold gently but nobody "
            "explain what that suppose to feel like.\n"
            "After a year of practicing I can finally makes three dishes good. My "
            "roommate still wont eat my curry tho because one time I mistaked cayenne "
            "for paprika and nearly sended him to the moon."
        ),
        "forbidden": (
            "cook dont happen", "it happen one", "omelet losed", "become scrambled",
            "Recipes always says", "nobody explain", "that suppose to",
            "I can finally makes", "three dishes good", "still wont eat",
            "curry tho", "I mistaked", "nearly sended",
        ),
    },
    {
        "id": "essay-07-bicycles",
        "text": (
            "Riding a bicycle to work save money and keep you fit but it aint always "
            "easy. Drivers dont see cyclists sometime and the bike lanes disappears "
            "right when you needs them most. My helmet have saved my head twice all "
            "ready this year.\n"
            "The city promise new lanes for three years now and nothing never got "
            "built. Cyclists keeps signing petitions but the council table the issue "
            "every single meeting like clock work."
        ),
        "forbidden": (
            "bicycle to work save", "keep you fit", "aint always easy",
            "see cyclists sometime", "lanes disappears", "you needs them",
            "helmet have saved", "all ready this year", "city promise new",
            "nothing never got", "Cyclists keeps", "council table the",
            "clock work",
        ),
    },
    {
        "id": "essay-08-ocean",
        "text": (
            "The ocean cover most of the planet but peoples knows less about it then "
            "the moon. Divers whom explore deep reefs finds new species almost every "
            "trip. Some fish glows in colors that dont even got names yet.\n"
            "Plastic waste have became the oceans biggest threat. Sea turtles mistakes "
            "floating bags for jellyfish and birds feeds bottle caps too there chicks. "
            "Cleaning it up require effort from every country not just the coastal "
            "ones."
        ),
        "forbidden": (
            "ocean cover most", "peoples knows", "it then the moon",
            "Divers whom explore", "reefs finds", "fish glows",
            "dont even got", "waste have became", "oceans biggest",
            "turtles mistakes", "birds feeds", "caps too there chicks",
            "up require effort",
        ),
    },
    {
        "id": "essay-09-lunch",
        "text": (
            "School lunches has improved but students still complains about them "
            "dayly. The cafeteria serve pizza every friday witch is the only day "
            "nobody trade away there tray. Vegetables mostly goes in the garbage no "
            "matter how the cooks prepares them.\n"
            "A new salad bar were installed last fall and it surprise everyone by "
            "being popular. Turns out kids dont hate greens, they hates soggy greens "
            "what sat under heat lamps since morning."
        ),
        "forbidden": (
            "lunches has improved", "students still complains", "dayly",
            "cafeteria serve", "friday witch is", "nobody trade",
            "away there tray", "mostly goes", "cooks prepares",
            "salad bar were installed", "it surprise everyone",
            "they hates", "greens what sat",
        ),
    },
    {
        "id": "essay-10-phones",
        "text": (
            "Smartphones changed how people talks to each other and not all ways for "
            "the better. Familys sits at dinner tables scrolling instead of speaking. "
            "Freinds sends memes but forgets birthdays even when the phone remind "
            "them.\n"
            "Still the devices does alot of good to. Grandparents video calls "
            "grandkids across oceans and lost hikers gets rescued because there phone "
            "pinged a tower. Like most tools it depend how you uses it."
        ),
        "forbidden": (
            "people talks", "not all ways for", "Familys sits", "Freinds sends",
            "but forgets", "phone remind them", "devices does alot",
            "good to.", "Grandparents video calls", "hikers gets",
            "there phone pinged", "it depend how", "you uses",
        ),
    },
    {
        "id": "essay-11-camping",
        "text": (
            "Camping sound relaxing untill you actually tries it the first time. "
            "Setting up a tent take twice as long as the instructions claims and the "
            "ground always have one rock exactly were your back go. Mosquitos finds "
            "you no matter how much spray you puts on.\n"
            "Even so the payoff are real. Stars looks brighter away from city lights "
            "and food taste better cooked over a fire. Most campers forgets the bad "
            "parts before they even gets home and starts planning the next trip."
        ),
        "forbidden": (
            "Camping sound relaxing", "untill you actually tries",
            "tent take twice", "instructions claims", "always have one rock",
            "were your back go", "Mosquitos finds", "you puts on",
            "payoff are real", "Stars looks", "food taste better",
            "campers forgets", "they even gets", "starts planning",
        ),
    },
    {
        "id": "essay-12-library",
        "text": (
            "The public library do alot more then lend books now days. Ours offers "
            "computer classes resume help and even sewing machines what you can "
            "borrow. The librarians knows every regular by name and holds books they "
            "thinks youll like.\n"
            "Funding keep getting cut tho and hours shrinked again this year. The "
            "branch dont open sundays no more witch hurt students whom depends on the "
            "free internet for there homework."
        ),
        "forbidden": (
            "library do alot", "more then lend", "now days", "machines what you",
            "librarians knows", "holds books they thinks", "youll like",
            "Funding keep getting", "hours shrinked", "dont open sundays no more",
            "witch hurt", "students whom depends", "for there homework",
        ),
    },
    {
        "id": "essay-13-birds",
        "text": (
            "Watching birds turn into a hobby faster then people expect. You puts up "
            "one feeder and suddenly your buying seed in fifty pound bags. Each specie "
            "have its own personality, chickadees darts in and out while jays bullies "
            "everyone else of the perch.\n"
            "In migration season rare visitors shows up and the whole neighborhood "
            "chat group light up with photos. Last spring a painted bunting spended "
            "three days at are feeder and strangers drived from two towns over to "
            "seen it."
        ),
        "forbidden": (
            "birds turn into", "faster then people expect", "You puts up",
            "your buying seed", "Each specie have", "chickadees darts",
            "jays bullies", "else of the perch", "visitors shows",
            "group light up", "bunting spended", "at are feeder",
            "strangers drived", "to seen it",
        ),
    },
    {
        "id": "essay-14-exercise",
        "text": (
            "Everybody know exercise matter but starting are the hardest part. Gyms "
            "fills up every january and empties out by march like clock work. People "
            "buys memberships thinking money spent will forces them to go but it "
            "dont work that way.\n"
            "Trainers says the trick are starting smaller then you think you should. "
            "A ten minute walk what actually happen beat an hour workout what dont. "
            "Habits builds slowly and motivation follow action not the other way "
            "round."
        ),
        "forbidden": (
            "Everybody know exercise", "exercise matter", "starting are the",
            "Gyms fills", "empties out by march", "clock work", "People buys",
            "will forces them", "it dont work", "Trainers says the trick are",
            "smaller then you", "walk what actually happen",
            "workout what dont", "Habits builds", "motivation follow",
        ),
    },
    {
        "id": "essay-15-movies",
        "text": (
            "Going to the movies use to be a weekly thing for my family but ticket "
            "prices has gotten crazy. Four people cant hardly get out the door for "
            "less then eighty dollars once you adds popcorn. Streaming at home dont "
            "got the same magic tho.\n"
            "Theres something about a dark theater full of strangers laughing at the "
            "same joke that a living room cant never match. Me and my dad still goes "
            "to one matinee a month, its are tradition and neither of us wants to "
            "brake it."
        ),
        "forbidden": (
            "movies use to be", "prices has gotten", "cant hardly get",
            "less then eighty", "you adds popcorn", "dont got the same",
            "magic tho", "Theres something", "cant never match",
            "Me and my dad still goes", "its are tradition", "to brake it",
        ),
    },
    {
        "id": "essay-16-laundry",
        "text": (
            "Laundry seem simple untill a red sock turn every white shirt pink. The "
            "washing instructions on tags is wrote so small nobody reads them and the "
            "symbols looks like ancient code. Everyone shrinks a sweater once before "
            "they learns the hard way.\n"
            "Folding are the part most people puts off longest. Clean clothes sits in "
            "baskets for days wile the dryer run a second time to unwrinkle stuff "
            "what all ready dried."
        ),
        "forbidden": (
            "Laundry seem simple", "untill a red sock turn", "tags is wrote",
            "nobody reads them", "symbols looks", "before they learns",
            "Folding are the", "people puts off", "clothes sits",
            "wile the dryer run", "stuff what all ready dried",
        ),
    },
    {
        "id": "essay-17-neighbors",
        "text": (
            "Good neighbors makes a street feel like home and bad ones makes you "
            "check the listings. Ours brings over tomatoes every august and watches "
            "are dog when we travels. In return we shovels there walk when it snow "
            "because there both getting up in years.\n"
            "The family across the street though plays drums at midnight and parks "
            "there truck across two driveways. Talking to them dont accomplish "
            "nothing, they nods polite and changes absolutely nothing about what "
            "they does."
        ),
        "forbidden": (
            "neighbors makes a street", "ones makes you", "Ours brings",
            "watches are dog", "when we travels", "we shovels there walk",
            "when it snow", "there both getting", "parks there truck",
            "dont accomplish nothing", "they nods polite", "what they does",
        ),
    },
    {
        "id": "essay-18-trains",
        "text": (
            "Trains use to be how everyone crossed the country and some folks says "
            "they should be again. Riding one now feel like time travel, you watches "
            "towns and fields slides past while somebody else worry about the "
            "driving. The dining car serve real food on real plates witch beats "
            "airline pretzels any day.\n"
            "The problem are the schedule. Freight gets priority on most tracks so "
            "passenger trains sits on sidings for hours. My last trip arrived five "
            "hour late and nobody even apologized or explain why."
        ),
        "forbidden": (
            "Trains use to be", "folks says", "now feel like", "you watches",
            "fields slides", "somebody else worry", "car serve real",
            "witch beats", "problem are the schedule", "trains sits",
            "five hour late", "or explain why",
        ),
    },
    {
        "id": "essay-19-pizza",
        "text": (
            "Every town swear there local pizza place are the best and every town are "
            "wrong except mine. The dough rise over night and the sauce recipe aint "
            "changed since the seventies. Slices comes out to big for the paper "
            "plates witch is exactly how it suppose to be.\n"
            "Chain pizza dont even compare tho its cheaper. The crust taste like "
            "cardboard and the cheese have that weird plastic shine. People whom "
            "grews up on real pizza can tell the difference blind folded."
        ),
        "forbidden": (
            "town swear there local", "place are the best", "town are wrong",
            "dough rise over night", "aint changed", "Slices comes out to big",
            "witch is exactly", "it suppose to be", "dont even compare tho",
            "crust taste like", "cheese have that", "People whom grews",
            "blind folded",
        ),
    },
    {
        "id": "essay-20-sleep",
        "text": (
            "Sleep effect everything from memory to mood but most adults dont gets "
            "enough of it. Doctors recommends eight hours wile the average person "
            "scrape by on six. Screens is partly too blame because the blue light "
            "tricks brains into thinking its still day time.\n"
            "Fixing your sleep dont require no fancy gadgets. Going to bed at the "
            "same time every night matter more then any app. Keep the room cool and "
            "dark, put the phone acrost the room, and mornings stops feeling like a "
            "punishment."
        ),
        "forbidden": (
            "Sleep effect everything", "adults dont gets", "Doctors recommends",
            "wile the average", "person scrape by", "Screens is partly too blame",
            "its still day time", "dont require no fancy", "every night matter",
            "more then any app", "phone acrost the", "mornings stops",
        ),
    },
)
