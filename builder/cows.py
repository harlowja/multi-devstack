import random

# Thx cowsay
# See: http://www.nog.net/~tony/warez/cowsay.shtml
COWS = dict()
COWS['happy'] = r'''
{header}
        \   {ear}__{ear}
         \  ({eye}{eye})\_______
            (__)\       )\/\
                ||----w |
                ||     ||
'''
COWS['unhappy'] = r'''
{header}
  \         ||       ||
    \    __ ||-----mm||
      \ (  )/_________)//
        ({eye}{eye})/
        {ear}--{ear}
'''
FAILS = [
    [
        " __________",
        "< Failure! >",
        " ----------"
    ],
    [
        " _____________________",
        "/ We were in the nick \\",
        "| of time. You were   |",
        "\\ in great peril.     /",
        " ---------------------"
    ],
    [
        " ___________________",
        "/ I know a dead     \\",
        "| parrot when I see |",
        "| one, and I'm      |",
        "| looking at one    |",
        "\\ right now.        /",
        " -------------------"
    ],
    [
        " _________________",
        "/ Welcome to the  \\",
        "| National Cheese |",
        "\\ Emporium        /",
        " -----------------"
    ],
    [
        " ______________________",
        "/ What is the airspeed \\",
        "| velocity of an       |",
        "\\ unladen swallow?     /",
        " ----------------------"
    ],
    [
        " ______________________",
        "/ Now stand aside,     \\",
        "\\ worthy adversary.    /",
        " ----------------------"
    ],
    [
        " ___________________",
        "/ Okay, we'll call  \\",
        "\\ it a draw.        /",
        " -------------------"
    ],
    [
        " _______________",
        "/ She turned me \\",
        "\\ into a newt!  /",
        " ---------------"
    ],
    [
        " ___________________",
        "< Fetchez la vache! >",
        " -------------------"
    ],
    [
        " __________________________",
        "/ We'd better not risk     \\",
        "| another frontal assault, |",
        "\\ that rabbit's dynamite.  /",
        " --------------------------"
    ],
    [
        " ______________________",
        "/ This is supposed to  \\",
        "| be a happy occasion. |",
        "| Let's not bicker and |",
        "| argue about who      |",
        "\\ killed who.          /",
        " ----------------------"
    ],
    [
        " ___________",
        "< Run away! >",
        " -----------"
    ],
    [
        " _______________________",
        "< You have been borked. >",
        " -----------------------"
    ],
    [
        " __________________",
        "/ We used to dream  \\",
        "| of living in a    |",
        "\\ corridor!         /",
        " -------------------"
    ],
    [
        " ______________",
        "/ You will not \\",
        "| go to space  |",
        "\\ today...     /",
        " --------------"
    ],
    [
        " ______________________",
        "/ NOBODY expects the   \\",
        "\\ Spanish Inquisition! /",
        " ----------------------"
    ],
    [
        " ______________________",
        "/ Spam spam spam spam  \\",
        "\\ baked beans and spam /",
        " ----------------------"
    ],
    [
        " ____________________",
        "/ Brave Sir Robin    \\",
        "\\ ran away.          /",
        " --------------------"
    ],
    [
        " _______________________",
        "< Message for you, sir. >",
        " -----------------------"
    ],
    [
        " ____________________",
        "/ We are the knights \\",
        "\\ who say.... NI!    /",
        " --------------------"
    ],
    [
        " ____________________",
        "/ Now go away or I   \\",
        "| shall taunt you a  |",
        "\\ second time.       /",
        " --------------------"
    ],
    [
        " ____________________",
        "/ It's time for the  \\",
        "| penguin on top of  |",
        "| your television to |",
        "\\ explode.           /",
        " --------------------"
    ]
]
OKS = [
    [
        " ___________",
        "/ You shine \\",
        "| out like  |",
        "| a shaft   |",
        "| of gold   |",
        "| when all  |",
        "| around is |",
        "\\ dark.     /",
        " -----------"
    ],
    [
        " ______________________________",
        "< I'm a lumberjack and I'm OK. >",
        " ------------------------------"
    ],
    [
        " ____________________",
        "/ Australia!         \\",
        "| Australia!         |",
        "| Australia!         |",
        "\\ We love you, amen. /",
        " --------------------"
    ],
    [
        " ______________",
        "/ Say no more, \\",
        "| Nudge nudge  |",
        "\\ wink wink.   /",
        " --------------"
    ],
    [
        " ________________",
        "/ And there was  \\",
        "\\ much rejoicing /",
        " ----------------"
    ],
    [
        " __________",
        "< Success! >",
        " ----------"
    ]
]


def goodbye_header(worked):
    if worked:
        lines = random.choice(OKS)
    else:
        lines = random.choice(FAILS)
    return "\n".join(lines)


def goodbye(worked):
    cow = COWS['happy']
    eye_fmt = 'o'
    ear = "^"
    if not worked:
        cow = COWS['unhappy']
        eye_fmt = "o"
        ear = "v"
    else:
        cow = COWS['happy']
        eye_fmt = 'o'
        ear = "^"
    cow = cow.strip("\n\r")
    header = goodbye_header(worked)
    msg = cow.format(eye=eye_fmt, ear=ear, header=header)
    print(msg)
