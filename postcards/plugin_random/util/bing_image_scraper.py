import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

# code by
# stephenhouser/Bing-Image-Scraper
# https://gist.github.com/stephenhouser/c5e2b921c3770ed47eb3b75efbc94799


def _get_soup(url, header):
    # return BeautifulSoup(urllib2.urlopen(urllib2.Request(url,headers=header)),
    # 'html.parser')
    return BeautifulSoup(
        urllib.request.urlopen(urllib.request.Request(url, headers=header)), "html.parser"
    )


if __name__ == "__main__":
    query_parts: list[str] = sys.argv[1].split()
    url = (
        "http://www.bing.com/images/search?q="
        + "+".join(query_parts)
        + "+filterui:imagesize-large&FORM=HDRSC2&adlt=off"
    )

    # add the directory for your image here
    DIR = "Pictures"
    header = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/43.0.2357.134 Safari/537.36"
    }
    soup = _get_soup(url, header)

    ActualImages = []  # contains the link for Large original images, type of  image
    for a in soup.find_all("a", {"class": "iusc"}):
        # print a
        mad = json.loads(a["mad"])
        turl = mad["turl"]
        m = json.loads(a["m"])
        murl = m["murl"]

        image_name = urllib.parse.urlsplit(murl).path.split("/")[-1]
        print(image_name)

        ActualImages.append((image_name, turl, murl))

    print("there are total", len(ActualImages), "images")

    if not os.path.exists(DIR):
        os.mkdir(DIR)

    DIR = os.path.join(DIR, query_parts[0])
    if not os.path.exists(DIR):
        os.mkdir(DIR)

    ##print images
    for _i, (image_name, _turl, murl) in enumerate(ActualImages):
        try:
            raw_img = urllib.request.urlopen(murl).read()

            cntr = len([i for i in os.listdir(DIR) if image_name in i]) + 1
            # print cntr

            with open(os.path.join(DIR, image_name), "wb") as f:
                f.write(raw_img)
            print("downloaded " + murl)
        except Exception as e:
            print("could not load : " + image_name)
            print(e)
