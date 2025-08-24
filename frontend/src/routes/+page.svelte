<script lang="ts">
  import {onMount, tick} from 'svelte';
  import {fetchStubs, mpvControl, SUB_MODES, type Subtitle} from '$lib';

  let currentSubMode = $state(0); // Index in SUB_MODES

  let connected = $state(true);
  let subtitles = $state<Subtitle[]>([]);
  let secondarySubtitles = $state<Subtitle[]>([]);
  let activeSubtitleStart = $state<number | null>(null);
  let sentenceStartPad = $state(500); // ms
  let sentenceEndPad = $state(500); // ms
  let updating = $state(false);

  let selectedSubtitles = $state<Set<Subtitle>>(new Set());
  let selectedSubtitlesSentence = $derived(
    Array.from(selectedSubtitles).sort((a, b) => a.start - b.start).map((sub) => sub.text).join(' '));

  // Auto-scroll to active subtitle, therefore need to depend on active subtitle.
  $effect(() => {
    activeSubtitleStart;
    tick().then(() => {
      document.querySelector('div[data-active="true"]')?.scrollIntoView(
        {behavior: 'smooth', inline: 'center', block: 'center'});
    })
  })

  // Connect to event source on mount
  onMount(() => {
    // Event source that provides updates like current subtitle etc...
    const eventSource = new EventSource('/data');

    // Called when event source receives messages
    eventSource.onmessage = function (e) {
      console.log('[RECEIVED] ' + e.data);
      const msg = e.data;
      if (msg.length < 1) {
        return;
      }

      const cmd = msg[0];
      switch (cmd) {
        case 's': // Subtitle update
          activeSubtitleStart = parseInt(msg.slice(1));
          break;
        case 'r': // Reload page
          location.reload();
          break;
        default:
          console.log('[WARNING] Unknown command: ' + cmd);
          break;
      }
    };

    eventSource.onerror = function (e) {
      console.log('[ERROR] ' + e);
      connected = false;
      eventSource.close();
    };

    eventSource.onopen = function () {
      console.log('[OPEN]');
    }

    return () => eventSource.close();
  });

  // Request subtitles on mount
  onMount(async () => {
    subtitles = await fetchStubs('/subs');
    secondarySubtitles = await fetchStubs('/secondary_subs');
  });

  function onKeyDown(event: KeyboardEvent) {
    // Space bar, toggle pause
    if (event.code == "Space") {
      mpvControl('cycle', ['pause']);
      event.preventDefault();
    }

    // a/Left arrow key, go to last sub, ctrl: last chapter
    else if (event.code == "KeyA" || event.code == "ArrowLeft") {
      if (!event.ctrlKey) {
        mpvControl('sub-seek', [-1]);
      } else {
        mpvControl('add', ['chapter', -1]);
      }
      event.preventDefault();
    }

    // d/Right arrow key, go to next sub, ctrl: next chapter
    else if (event.code == "KeyD" || event.code == "ArrowRight") {
      if (!event.ctrlKey) {
        mpvControl('sub-seek', [+1]);
      } else {
        mpvControl('add', ['chapter', +1]);
      }
      event.preventDefault();
    }

    // s/Down arrow key, go to start of current sub, ctrl: restart chapter
    else if (event.code == "KeyS" || event.code == "ArrowDown") {
      if (!event.ctrlKey) {
        mpvControl('sub-seek', [0]);
      } else {
        mpvControl('add', ['chapter', 0]);
      }
      event.preventDefault();
    }

    // w/v/Up arrow key, toggle sub visibility
    else if (event.code == "KeyW" || event.code == "KeyV" || event.code == "ArrowUp") {
      mpvControl('cycle', ['sub-visibility']);
      event.preventDefault();
    }
  }

  function seek(sub: Subtitle) {
    return (_: MouseEvent) => {
      mpvControl('seek', [sub.start / 1000, 'absolute']);
      activeSubtitleStart = sub.start;
    }
  }

  function formatTime(millis: number) {
    const toPaddedString = (number: number) => number.toString().padStart(2, '0');
    const hours = Math.floor(millis / 3600000);
    millis %= 3600000;
    const minutes = Math.floor(millis / 60000);
    millis %= 60000;
    const seconds = Math.floor(millis / 1000);
    return `${hours > 0 ? toPaddedString(hours) + ':' : ''}${toPaddedString(minutes)}:${toPaddedString(seconds)}`;
  }

  function toggleSelect(sub: Subtitle) {
    return (event: MouseEvent) => {
      if (event.target instanceof HTMLElement && event.target.getAttribute("role") !== "checkbox") {
        // Clicked on a child element that is not the checkbox itself, ignore
        return;
      }

      if (selectedSubtitles.has(sub)) {
        selectedSubtitles.delete(sub);
      } else {
        selectedSubtitles.add(sub);
      }
      // Re-assign the set to trigger reactivity
      selectedSubtitles = new Set(selectedSubtitles);
    }
  }

  function updateSubMode() {
    mpvControl('script-message', ['@migakulua', 'sub_mode', SUB_MODES[currentSubMode].toLowerCase()])
  }

  function clearSelection() {
    selectedSubtitles = new Set();
  }

  async function updateAnkiCard() {
    const orderedSubs = Array.from(selectedSubtitles).sort((a, b) => a.start - b.start);
    const startTime = orderedSubs[0].start;
    const endTime = orderedSubs[orderedSubs.length - 1].end;

    // Find secondary subs that overlap with the selected subs
    // They are ordered, we could binary search and make this faster
    const overlappingSecondarySubs = secondarySubtitles.filter((sub) => {
      return !(sub.end < startTime || sub.start > endTime);
    });

    // Combine secondary subs into one text
    const secondaryText = overlappingSecondarySubs.map((sub) => sub.text).join(' ');

    // Send to backend
    updating = true;
    await fetch('./anki', {
      method: 'POST',
      headers: {
        'Content-Type': 'text/plain;charset=UTF-8',
      },
      body: JSON.stringify({
        'translation_text': secondaryText,
        'start': startTime - sentenceStartPad,
        'end': endTime + sentenceEndPad,
      }),
    });
    updating = false;
  }
</script>

<svelte:window onkeydown={onKeyDown}/>

{#if !connected}
    <h2 class="text-2xl">MPV Connection Closed.</h2>
{:else}
    <!-- Header -->
    <div class="sticky top-0 w-full flex gap-4 bg-gray-900/80 backdrop-blur-sm p-4 z-10
            border-b border-gray-700 select-none">
        <!-- Sub mode -->
        <div>
            <label class="inline-block text-gray-500 font-bold" for="submode">
                Sub mode:
            </label>
            <select id="submode" bind:value={currentSubMode} onchange={updateSubMode} class="bg-gray-900">
                {#each SUB_MODES as subMode, index}
                    <option value={index}>{subMode}</option>
                {/each}
            </select>
        </div>

        <!-- Sentence start pad -->
        <div>
            <label class="inline-block text-gray-500 font-bold" for="startPad">
                Start pad:
            </label>
            <input id="startPad" type="number" bind:value={sentenceStartPad} class="bg-gray-900 w-14"/>
        </div>

        <!-- Sentence end pad -->
        <div>
            <label class="inline-block text-gray-500 font-bold" for="endPad">
                End pad:
            </label>
            <input id="endPad" type="number" bind:value={sentenceEndPad} class="bg-gray-900 w-14"/>
        </div>
    </div>

    <!-- Subtitles -->
    <div class="flex flex-col p-4 gap-4">
        {#each subtitles as sub}
            <!-- Sub card -->
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_interactive_supports_focus -->
            <div class="text-2xl p-4 border-4 rounded-2xl cursor-pointer aria-checked:border-indigo-700!
                {sub.start === activeSubtitleStart ?
                    'bg-gray-900 border-gray-700' : 'border-gray-900 hover:border-gray-800'}"
                 data-active={sub.start === activeSubtitleStart}
                 role="checkbox"
                 onclick={toggleSelect(sub)}
                 aria-checked={selectedSubtitles.has(sub)}
            >
                <!-- Sub text -->
                {#key sub.text}
                    <span>
                        {sub.text}
                    </span>
                {/key}

                <!-- Timestamps -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <span class="block text-xs w-fit text-gray-500 cursor-pointer force-hover-underline"
                      onclick={seek(sub)}>
                    {formatTime(sub.start)} - {formatTime(sub.end)}
                </span>
            </div>
        {/each}
    </div>

    <!-- Footer. -->
    {#if selectedSubtitles.size > 0}
        <!-- We have to use #key here, otherwise Migaku wont re-read the sentence as it changes -->
        {#key selectedSubtitlesSentence}
            <div class="fixed bottom-0 w-full flex flex-col gap-4 bg-gray-900/80 backdrop-blur-sm p-4 z-10
                    border-t border-gray-700">
                <!-- Selected sentence -->
                <span class="text-xl">
                    {selectedSubtitlesSentence}
                </span>

                <!-- Controls -->
                <div class="flex justify-stretch gap-4">
                    <!-- Clear selection -->
                    <button onclick={clearSelection}
                            class="w-full font-bold rounded-full p-3 transition-all
                                hover:scale-105 bg-gray-800  text-gray-500 cursor-pointer">
                        Clear Selection
                    </button>

                    <!-- Update anki card -->
                    {#if !updating}
                        <button class="w-full font-bold rounded-full p-3 transition-all hover:scale-105
                            bg-indigo-700 text-gray-50 cursor-pointer"
                                onclick={updateAnkiCard}
                        >
                            Update Anki Card
                        </button>
                    {:else}
                        <button class="w-full font-bold rounded-full p-3 transition-all
                              bg-indigo-700 text-gray-50 opacity-50 cursor-not-allowed"
                                disabled
                        >
                            Updating...
                        </button>
                    {/if}
                </div>
            </div>
        {/key}
    {/if}
{/if}