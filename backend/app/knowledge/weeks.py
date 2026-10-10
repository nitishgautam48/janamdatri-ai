"""Week-by-week pregnancy guide data (weeks 4–40).

Sizes use familiar Indian fruits and vegetables. Lengths are approximate (crown–rump
until week 19, crown–heel from week 20) and only for orientation.
"""
from __future__ import annotations

# week: (size_en, size_hi, length_cm, weight_g, milestone_en, milestone_hi)
WEEKS: dict[int, tuple[str, str, float, int | None, str, str]] = {
    4: ("poppy seed", "खसखस का दाना", 0.1, None, "The pregnancy has just implanted. A missed period is often the first sign.", "गर्भ अभी ठहरा है। माहवारी का रुकना पहला संकेत होता है।"),
    5: ("sesame seed", "तिल का दाना", 0.2, None, "Baby's brain, spine and heart are starting to form.", "बच्चे का दिमाग़, रीढ़ और दिल बनना शुरू हो रहा है।"),
    6: ("lentil (masoor dal)", "मसूर की दाल", 0.6, None, "A tiny heartbeat can begin this week.", "इस हफ़्ते छोटी-सी धड़कन शुरू हो सकती है।"),
    7: ("chickpea (chana)", "चना", 1.0, None, "Hands and feet are forming as tiny buds.", "हाथ-पैर छोटी कलियों जैसे बन रहे हैं।"),
    8: ("kidney bean (rajma)", "राजमा", 1.6, 1, "Fingers are forming and baby has started to move, though you can't feel it yet.", "उँगलियाँ बन रही हैं, बच्चा हिलने लगा है, पर अभी महसूस नहीं होगा।"),
    9: ("grape (angoor)", "अंगूर", 2.3, 2, "All essential organs have begun to form.", "सभी ज़रूरी अंग बनने लगे हैं।"),
    10: ("gooseberry (amla)", "आँवला", 3.1, 4, "Baby is now called a fetus. Tiny nails are forming.", "अब बच्चा 'भ्रूण' कहलाता है। नन्हे नाखून बन रहे हैं।"),
    11: ("fig (anjeer)", "अंजीर", 4.1, 7, "Baby can open and close its fists.", "बच्चा मुट्ठी खोल-बंद कर सकता है।"),
    12: ("lime (nimbu)", "नींबू", 5.4, 14, "Reflexes develop. The risk of miscarriage drops after this week.", "बच्चे में प्रतिक्रियाएँ बन रही हैं। इसके बाद गर्भपात का ख़तरा कम हो जाता है।"),
    13: ("pea pod (matar ki phali)", "मटर की फली", 7.4, 23, "Vocal cords are forming. Nausea often eases now.", "आवाज़ की नली बन रही है। अक्सर उल्टी-मतली अब कम होने लगती है।"),
    14: ("lemon", "बड़ा नींबू", 8.7, 43, "Baby can squint and frown. Second trimester begins.", "बच्चा आँखें सिकोड़ सकता है। दूसरी तिमाही शुरू।"),
    15: ("apple (seb)", "सेब", 10.1, 70, "Baby can sense light through closed eyelids.", "बंद पलकों से भी बच्चा रोशनी महसूस कर सकता है।"),
    16: ("guava (amrood)", "अमरूद", 11.6, 100, "Baby's heart pumps about 25 litres of blood a day.", "बच्चे का दिल रोज़ लगभग 25 लीटर खून पंप करता है।"),
    17: ("pomegranate (anaar)", "अनार", 13.0, 140, "Baby is building fat to keep warm after birth.", "जन्म के बाद गर्म रहने के लिए बच्चे में चर्बी बन रही है।"),
    18: ("sweet potato (shakarkandi)", "शकरकंदी", 14.2, 190, "You may feel the first flutters of movement (quickening).", "आपको पहली बार हल्की हलचल महसूस हो सकती है।"),
    19: ("mango (aam)", "आम", 15.3, 240, "A protective coating (vernix) covers baby's skin.", "बच्चे की त्वचा पर सुरक्षा की परत बन रही है।"),
    20: ("banana (kela)", "केला", 25.6, 300, "Halfway there! The anomaly scan is usually done around now.", "आधा सफ़र पूरा! इस समय के आसपास एनॉमली स्कैन होता है।"),
    21: ("carrot (gajar)", "गाजर", 26.7, 360, "Baby swallows amniotic fluid and is learning to digest.", "बच्चा पानी निगलकर पचाना सीख रहा है।"),
    22: ("small papaya (papita)", "छोटा पपीता", 27.8, 430, "Baby's sense of touch is developing; eyebrows appear.", "छूने की समझ बन रही है; भौंहें दिखने लगी हैं।"),
    23: ("large mango", "बड़ा आम", 28.9, 500, "Baby can hear loud sounds outside the womb.", "बच्चा बाहर की तेज़ आवाज़ें सुन सकता है।"),
    24: ("corn cob (bhutta)", "भुट्टा", 30.0, 600, "Baby is about 30 cm long, responds to sound and touch, and can hear your voice now.", "बच्चा लगभग 30 सेमी का है, आवाज़ और स्पर्श पर प्रतिक्रिया देता है, और आपकी आवाज़ सुन सकता है।"),
    25: ("cauliflower (phool gobhi)", "फूलगोभी", 34.6, 660, "Baby's hands are fully formed and can grasp.", "बच्चे के हाथ पूरे बन गए हैं और पकड़ सकते हैं।"),
    26: ("bottle gourd, small (lauki)", "छोटी लौकी", 35.6, 760, "Eyes begin to open. Lungs are developing quickly.", "आँखें खुलने लगती हैं। फेफड़े तेज़ी से बन रहे हैं।"),
    27: ("cabbage (patta gobhi)", "पत्ता गोभी", 36.6, 875, "Baby has regular sleep and wake times.", "बच्चे के सोने-जागने का समय बनने लगा है।"),
    28: ("brinjal (baingan)", "बैंगन", 37.6, 1000, "Third trimester begins. Start counting baby's kicks every day.", "तीसरी तिमाही शुरू। रोज़ बच्चे की हलचल गिनना शुरू करें।"),
    29: ("small pumpkin (kaddu)", "छोटा कद्दू", 38.6, 1150, "Muscles and lungs keep maturing; kicks get stronger.", "मांसपेशियाँ और फेफड़े पक रहे हैं; लातें तेज़ होती हैं।"),
    30: ("large cabbage", "बड़ी पत्ता गोभी", 39.9, 1300, "Baby's brain is growing fast.", "बच्चे का दिमाग़ तेज़ी से बढ़ रहा है।"),
    31: ("coconut (nariyal)", "नारियल", 41.1, 1500, "Baby can turn its head from side to side.", "बच्चा सिर दाएँ-बाएँ घुमा सकता है।"),
    32: ("pineapple (ananas)", "अनानास", 42.4, 1700, "Baby practises breathing movements.", "बच्चा साँस लेने का अभ्यास करता है।"),
    33: ("large papaya", "बड़ा पपीता", 43.7, 1900, "Bones are hardening, except the skull which stays soft for birth.", "हड्डियाँ मज़बूत हो रही हैं, सिर की हड्डी जन्म के लिए नरम रहती है।"),
    34: ("muskmelon (kharbooja)", "ख़रबूज़ा", 45.0, 2100, "Baby's lungs are almost mature.", "बच्चे के फेफड़े लगभग तैयार हैं।"),
    35: ("honeydew melon", "बड़ा ख़रबूज़ा", 46.2, 2400, "Baby is gaining about 200 g a week.", "बच्चा हर हफ़्ते लगभग 200 ग्राम बढ़ रहा है।"),
    36: ("bottle gourd (lauki)", "लौकी", 47.4, 2600, "Baby may move head-down. Keep your hospital bag ready.", "बच्चा सिर नीचे कर सकता है। अस्पताल का बैग तैयार रखें।"),
    37: ("winter melon (petha)", "पेठा", 48.6, 2900, "Baby is considered early term.", "बच्चा अब लगभग पूरे समय का माना जाता है।"),
    38: ("pumpkin (kaddu)", "कद्दू", 49.8, 3100, "Baby's organs are ready for life outside.", "बच्चे के अंग बाहर की दुनिया के लिए तैयार हैं।"),
    39: ("small watermelon (tarbooz)", "छोटा तरबूज़", 50.7, 3300, "Full term. Watch for signs of labour.", "पूरा समय। प्रसव के संकेतों पर ध्यान दें।"),
    40: ("watermelon (tarbooz)", "तरबूज़", 51.2, 3400, "Your due date! Only a few babies arrive exactly today.", "आपकी नियत तारीख़! बहुत कम बच्चे ठीक आज आते हैं।"),
}

# Week-range focus items shown on the home screen ("This Week's Focus").
FOCUS: list[dict] = [
    {"from": 4, "to": 12, "kind": "RECOMMENDATION", "en": "Register your pregnancy and get your MCP card at the Anganwadi or sub-centre", "hi": "आंगनवाड़ी या उप-केंद्र पर गर्भावस्था पंजीकरण कराएँ और MCP कार्ड लें"},
    {"from": 4, "to": 13, "kind": "RECOMMENDATION", "en": "Take one folic acid tablet daily", "hi": "रोज़ एक फ़ोलिक एसिड की गोली लें"},
    {"from": 11, "to": 14, "kind": "RECOMMENDATION", "en": "Dating / NT ultrasound if your doctor advises", "hi": "डॉक्टर कहें तो पहला अल्ट्रासाउंड कराएँ"},
    {"from": 14, "to": 40, "kind": "RECOMMENDATION", "en": "Iron–folic acid (1 tablet) and calcium (2 tablets) daily, at different times", "hi": "रोज़ आयरन (1 गोली) और कैल्शियम (2 गोली), अलग-अलग समय पर"},
    {"from": 14, "to": 26, "kind": "RECOMMENDATION", "en": "One deworming tablet (albendazole) in the second trimester, as advised", "hi": "दूसरी तिमाही में एक बार पेट के कीड़ों की दवा (सलाह अनुसार)"},
    {"from": 16, "to": 24, "kind": "RECOMMENDATION", "en": "Td (tetanus) injection as per your MCP card", "hi": "MCP कार्ड के अनुसार Td (टिटनेस) का टीका"},
    {"from": 18, "to": 22, "kind": "RECOMMENDATION", "en": "Anomaly ultrasound between weeks 18–22", "hi": "18–22 हफ़्ते के बीच एनॉमली अल्ट्रासाउंड"},
    {"from": 24, "to": 28, "kind": "RECOMMENDATION", "en": "Glucose test due between week 24–28", "hi": "24–28 हफ़्ते के बीच शुगर (ग्लूकोज़) जाँच"},
    {"from": 28, "to": 40, "kind": "RECOMMENDATION", "en": "Count baby's kicks daily: at least 10 movements in 12 hours", "hi": "रोज़ बच्चे की हलचल गिनें: 12 घंटे में कम से कम 10 बार"},
    {"from": 30, "to": 36, "kind": "RECOMMENDATION", "en": "Make a birth plan: facility, transport (102), money, blood donor, companion", "hi": "प्रसव योजना बनाएँ: अस्पताल, वाहन (102), पैसे, रक्तदाता, साथ जाने वाला"},
    {"from": 34, "to": 40, "kind": "RECOMMENDATION", "en": "Keep your hospital bag, MCP card, Aadhaar and bank details ready for JSY", "hi": "अस्पताल का बैग, MCP कार्ड, आधार और बैंक विवरण (JSY के लिए) तैयार रखें"},
    {"from": 37, "to": 40, "kind": "RECOMMENDATION", "en": "Know the signs of labour: regular pains, water breaking, bleeding", "hi": "प्रसव के संकेत जानें: नियमित दर्द, पानी आना, खून आना"},
]


def size_for_week(week: int) -> tuple[str, str, float, int | None, str, str]:
    week = max(4, min(40, week))
    return WEEKS[week]


def focus_for_week(week: int) -> list[dict]:
    return [f for f in FOCUS if f["from"] <= week <= f["to"]]
