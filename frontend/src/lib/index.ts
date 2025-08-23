export interface Subtitle {
  start: number;
  end: number;
  text: string;
}

export const SUB_MODES = ['Default', 'Reading', 'Recall', 'Hidden'];

export async function mpvControl(command: string, args: any[]) {
  await fetch('./mpv_control', {
    method: 'POST',
    headers: {
      'Content-Type': 'text/plain;charset=UTF-8',
    },
    body: JSON.stringify({'command': [command].concat(args)}),
  });
}

export async function fetchStubs(url: string): Promise<Subtitle[]> {
  function cleanSubText(sub: Subtitle): Subtitle {
    // Remove \n from subtitles text and trim them
    sub.text = sub.text.replace(/\n/g, ' ').trim();
    // Remove text inside () and （） unless it is the only text
    {
      let nonParenthesis = sub.text.replace(/\(.*?\)/g, '').replace(/（.*?）/g, '').trim();
      if (nonParenthesis.length > 0) {
        sub.text = nonParenthesis;
      }
    }
    return sub;
  }

  const response = await fetch(url);
  if (!response.ok) {
    console.error(`Failed to fetch subtitles from ${url}: ${response.statusText}`);
    return [];
  }
  return (await response.json()).map(cleanSubText);
}