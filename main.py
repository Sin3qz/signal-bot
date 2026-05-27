import traceback
from strategies.spytips_cool import spy_tips_cool


def saveText(subject=None, subject2=None, text=None):
    if not subject and not subject2 and not text:
        return

    with open("message.txt", "w") as d:
        if subject:
            d.write(subject + "\n\n")

        if subject2:
            d.write(subject2 + "\n\n")

        if text:
            d.write(text)


def main():
    s, s2, t = spy_tips_cool()

    if s is None and s2 is None and t is None:
        print("Skipped")
        return

    saveText(s, s2, t)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error = "".join(traceback.format_exception(e))
        saveText("Error", None, error)
