<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import ReportHeader from '../components/ReportHeader.vue';
import ReportFooter from '../components/ReportFooter.vue';
import MetricMap from '../components/MetricMap.vue';
import {
  fetchAvailableCountries,
  loadAvailableTopics,
  loadIndicators,
  loadIndicatorLookup,
  getPMTilesBounds,
  loadTagCoverage,
  type PMTilesBounds,
  type TagCoverageItem
} from '../services/dataService';
import {
  prettifyTopic,
  prettifyIndicator,
  topicConfig,
  buildUrls,
  getFigureTopic,
  setCurrentSchoolSubTopic
} from '../utils/helpers';

import Plotly from 'plotly.js-dist-min';
declare const pmtiles: any;

const selectedCountry = ref('');
const selectedTopic = ref('');
const topicId = ref(0);
const isEmbed = ref(false);
const isIframe2Col = ref(false);

onMounted(async () => {
  // Detect if we're embedded in an iframe or HDX window
  const inIframe = window.self !== window.top;
  const hasEmbedParam = new URLSearchParams(window.location.search).has('embed');
  const hasIframe2ColParam = new URLSearchParams(window.location.search).has('iframe-2col');
  
  // If URL has ?embed or ?iframe-2col but we're not in an iframe (e.g., HDX fullscreen opened new tab),
  // redirect to remove the parameter so users see the normal version
  if ((hasEmbedParam || hasIframe2ColParam) && !inIframe) {
    const newUrl = window.location.pathname + window.location.hash;
    window.history.replaceState(null, '', newUrl);
    isEmbed.value = false;
    isIframe2Col.value = false;
  } else {
    isEmbed.value = inIframe || hasEmbedParam;
    // Automatically use iframe-2col mode when inside an iframe
    isIframe2Col.value = inIframe || hasIframe2ColParam;
  }
  
  // Apply embed mode class to body for global styling
  if (isEmbed.value || isIframe2Col.value) {
    document.body.classList.add('embed-mode');
    
    // Notify parent window of content height changes (for iframe embedding)
    const notifyParentResize = () => {
      if (window.parent !== window) {
        const height = document.documentElement.scrollHeight;
        window.parent.postMessage({ type: 'resize', height }, '*');
      }
    };
    
    // Observe content changes to notify parent
    const resizeObserver = new ResizeObserver(() => {
      notifyParentResize();
    });
    resizeObserver.observe(document.documentElement);
    
    // Also notify on mount
    setTimeout(notifyParentResize, 1000);
  }
  
  const fetchedCountries = await fetchAvailableCountries();
  countries.value = fetchedCountries.map(c => ({ value: c.code, label: c.name }));

  if (countries.value.length > 0) {
    const defaultCountry = 'RWA';
    selectedCountry.value = countries.value.find(c => c.value === defaultCountry)?.value || countries.value[0].value;
  }

  topics.value = ['roads-all-highways', 'building-area'];

  handleHashRouting();
  window.addEventListener('hashchange', handleHashRouting);

  // Setup ResizeObserver for maps and containers
  setTimeout(() => {
    const resizeObserver = new ResizeObserver(() => {
      // Trigger map resize
      window.dispatchEvent(new Event('resize'));
      
      // Also trigger Plotly resize
      ['comparison-plot', 'currentness-plot', 'completeness-plot', 'tag-treemap'].forEach(id => {
        const el = document.getElementById(id);
        if (el) Plotly.Plots.resize(el);
      });
    });
    
    // Observe map containers
    document.querySelectorAll('[id="road_comparison_map"], [id="current_map"], [id="completeness_map"], #tag-treemap').forEach(el => {
      if (el) resizeObserver.observe(el);
    });
    
    // Observe main container for embed mode
    const mainContainer = document.querySelector('.page-content');
    if (mainContainer) resizeObserver.observe(mainContainer);
  }, 1000);
});

const countries = ref<{ value: string; label: string }[]>([]);
const topics = ref<string[]>([]);
const indicators = ref<string[]>([]);
const isLoading = ref(false);
const dataDate = ref('');

const pmtilesUrl = ref('');
const parquetUrl = ref('');

const bounds = ref<PMTilesBounds | null>(null);

const featureCount = ref('');
const totalLength = ref('');
const tile1Label = ref('km of roads');

const tagCoverage = ref<TagCoverageItem[]>([]);
const showTagCoverage = ref(false);

const filteredTagCoverage = computed(() => {
  const attrCompletenessIndicators = indicators.value.filter(i => i.startsWith('attribute-completeness_'));
  return tagCoverage.value.filter(item => 
    attrCompletenessIndicators.includes(item.indicator)
  );
});

const map1Lookup = ref<Record<string, number>>({});
const map1Avg = ref(0);
const map1Description = ref('');
const map2Lookup = ref<Record<string, number>>({});
const map2Avg = ref(0);
const map2Description = ref('');
const map3Lookup = ref<Record<string, number>>({});
const map3Avg = ref(0);
const map3Description = ref('');

const banner1Level = ref<'Low' | 'Medium' | 'High'>('Medium');
const banner2Level = ref<'Low' | 'Medium' | 'High'>('Medium');
const banner3Level = ref<'Low' | 'Medium' | 'High'>('Medium');

const map1Indicator = ref('');
const map2Indicator = ref('currentness');
const map3Indicator = ref('');

const map1Layer = ref('h3_hexgrid');
const map2Layer = ref('h3_hexgrid');
const map3Layer = ref('h3_hexgrid');

const schoolSwitchVisible = ref(false);
const schoolSubTopic = ref('operator');

function handleHashRouting() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  const parts = hash.split('/');
  const hCountry = parts[0];
  const hTopic = parts[1];

  if (hCountry && countries.value.some(c => c.value === hCountry)) {
    selectedCountry.value = hCountry;
  }
  if (hTopic && topics.value.includes(hTopic)) {
    selectedTopic.value = hTopic;
  }
}

function updateHash(country: string, topic: string) {
  if (!country || !topic) return;
  const newHash = `#/${country}/${topic}`;
  if (window.location.hash !== newHash) {
    window.history.pushState(null, '', newHash);
  }
}

watch(selectedCountry, async (newCountry) => {
  if (!newCountry) return;
  await loadCountry(newCountry, true);
});

watch(selectedTopic, async (newTopic) => {
  if (!newTopic || !selectedCountry.value) return;
  topicId.value++;
  await loadCountry(selectedCountry.value, false);
});

async function loadCountry(code: string, updateTopics: boolean) {
  isLoading.value = true;

  const urls = buildUrls(code, getCurrentTopic());
  pmtilesUrl.value = urls.pmtilesUrl;
  parquetUrl.value = urls.parquetUrl;

  try {
    const resp = await fetch(urls.parquetUrl, { method: 'HEAD' });
    const lastModified = resp.headers.get('last-modified');
    if (lastModified) {
      dataDate.value = new Date(lastModified).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    }
  } catch (e) {
    dataDate.value = '';
  }

  try {
    bounds.value = await getPMTilesBounds(urls.pmtilesUrl);
    if (!bounds.value) {
      isLoading.value = false;
      return;
    }

    if (updateTopics) {
      const availableTopics = await loadAvailableTopics(urls.parquetUrl);
      topics.value = availableTopics;
      selectedTopic.value = availableTopics.includes('roads-all-highways')
        ? 'roads-all-highways'
        : availableTopics[0] || '';
    }

    const topicName = selectedTopic.value;
    const newIndicators = await loadIndicators(urls.parquetUrl, topicName);
    indicators.value = newIndicators;

    showTagCoverage.value = newIndicators.some(ind => ind.startsWith('attribute-completeness_'));

    const cfg = topicConfig[topicName] || {
      comparisonIndicator: topicName.includes('building') ? 'building-comparison' : 'road-comparison',
      completenessIndicator: topicName.includes('building') ? 'user-activity' : 'attribute-completeness_surface'
    };

    map1Indicator.value = newIndicators.includes(cfg.comparisonIndicator)
      ? cfg.comparisonIndicator
      : newIndicators[0] || '';
    map2Indicator.value = 'currentness';
    map3Indicator.value = newIndicators.includes(cfg.completenessIndicator)
      ? cfg.completenessIndicator
      : newIndicators[0] || '';

    await loadMapData();

    const coverage = await loadTagCoverage(urls.parquetUrl);
    tagCoverage.value = coverage;

    loadTreemap();

    const isSchool = topicName.toLowerCase().startsWith('school');
    const isHospital = topicName.toLowerCase().startsWith('hospital');
    const isHealthcarePrimary = topicName.toLowerCase().startsWith('healthcare-primary');
    schoolSwitchVisible.value = isSchool || isHospital || isHealthcarePrimary;

    updateHash(code, topicName);
  } catch (e) {
    console.error('Failed to load country:', e);
  } finally {
    isLoading.value = false;
  }
}

function getCurrentTopic(): string {
  return selectedTopic.value || topics.value[0] || '';
}

async function loadMapData() {
  const topicName = getCurrentTopic();

  if (map1Indicator.value) {
    const result = await loadIndicatorLookup(parquetUrl.value, topicName, map1Indicator.value);
    map1Lookup.value = result.lookup;
    map1Avg.value = result.avg;
    map1Description.value = result.description;
    banner1Level.value = getBannerLevel(result.avg);
    loadPlot(map1Indicator.value, 'comparison-plot');
  }

  if (map2Indicator.value) {
    const result = await loadIndicatorLookup(parquetUrl.value, topicName, map2Indicator.value);
    map2Lookup.value = result.lookup;
    map2Avg.value = result.avg;
    map2Description.value = result.description;
    banner2Level.value = getBannerLevel(result.avg);
    loadPlot(map2Indicator.value, 'currentness-plot');
  }

  if (map3Indicator.value) {
    const result = await loadIndicatorLookup(parquetUrl.value, topicName, map3Indicator.value);
    map3Lookup.value = result.lookup;
    map3Avg.value = result.avg;
    map3Description.value = result.description;
    banner3Level.value = getBannerLevel(result.avg);
    loadPlot(map3Indicator.value, 'completeness-plot');
  }
}

function getBannerLevel(avg: number): 'Low' | 'Medium' | 'High' {
  if (avg < 0.25) return 'Low';
  if (avg < 0.75) return 'Medium';
  return 'High';
}

function getBannerColor(level: 'Low' | 'Medium' | 'High'): string {
  switch (level) {
    case 'Low': return '#F44336';
    case 'Medium': return '#FFEB3B';
    case 'High': return '#4CAF50';
  }
}

function getBannerTextColor(level: 'Low' | 'Medium' | 'High'): string {
  return level === 'Medium' ? '#333' : 'white';
}

async function loadPlot(indicator: string, containerId: string) {
  if (!indicator || !selectedCountry.value) return;

  const urls = buildUrls(selectedCountry.value, getCurrentTopic());
  const figureTopic = getFigureTopic(getCurrentTopic());
  const url = `${urls.figureBase}/adm0__${figureTopic}__${indicator}.json.gz`;

  try {
    const response = await fetch(url);
    if (!response.ok) return;

    const ds = new DecompressionStream('gzip');
    const decompressedStream = response.body!.pipeThrough(ds);
    const jsonText = await new Response(decompressedStream).text();
    const parsed = JSON.parse(jsonText);

    const container = Array.isArray(parsed) ? parsed[0] : parsed;
    const fig = container.figure || container;

    fig.layout = fig.layout || {};
    delete fig.layout.width;
    delete fig.layout.height;
    fig.layout.paper_bgcolor = 'rgba(0,0,0,0)';
    fig.layout.plot_bgcolor = 'rgba(0,0,0,0)';
    fig.layout.margin = { t: 40, r: 20, l: 40, b: 40 };
    fig.layout.font = { family: 'Archivo, sans-serif', size: 11 };
    if (fig.layout.title) {
      fig.layout.title.font = { size: 13 };
    }

    Plotly.react(containerId, fig.data, fig.layout, {
      responsive: true,
      displayModeBar: false
    });
  } catch (e) {
    console.error('Failed to load plot:', e);
  }
}

async function loadTreemap() {
  if (!selectedCountry.value) return;

  const urls = buildUrls(selectedCountry.value, getCurrentTopic());
  const treemapUrl = urls.treemapUrl;
  const countUrl = urls.countUrl;

  try {
    const [response, countResponse] = await Promise.all([
      fetch(treemapUrl),
      fetch(countUrl).catch(() => null)
    ]);

    if (!response.ok) return;

    const ds = new DecompressionStream('gzip');
    const jsonText = await new Response(response.body!.pipeThrough(ds)).text();
    const data = JSON.parse(jsonText);

    let countData = data;
    if (countResponse?.ok) {
      const countDs = new DecompressionStream('gzip');
      const countJsonText = await new Response(countResponse.body!.pipeThrough(countDs)).text();
      countData = JSON.parse(countJsonText);
    }

    const labels: string[] = [];
    const values: number[] = [];
    const colors: (string | undefined)[] = [];
    const textColors: (string | undefined)[] = [];

    const isArea = data.total_area !== undefined;
    const isLength = data.total_length !== undefined;
    const unitFactor = isArea ? 1_000_000 : (isLength ? 1_000 : 1);
    const unitSuffix = isArea ? ' km²' : (isLength ? ' km' : '');

    data.groupByResult?.forEach((item: any) => {
      const rawLabel = item.groupByObject;
      const value = (item.result?.[0]?.value || 0) / unitFactor;

      if (value === 0 && rawLabel.toLowerCase() !== 'remainder') return;

      let label = rawLabel;
      if (rawLabel.includes('isced:level=')) {
        label = 'Level ' + rawLabel.split('=')[1];
      } else if (rawLabel.includes('hospital:speciality=')) {
        label = rawLabel.split('=')[1];
      } else if (rawLabel.includes('=')) {
        label = rawLabel.split('=')[1];
      }
      labels.push(label);
      values.push(value);

      const isRemainder = rawLabel.toLowerCase() === 'remainder';
      colors.push(isRemainder ? '#888888' : undefined);
      textColors.push(isRemainder ? 'white' : undefined);
    });

    if (countData.total !== undefined) {
      featureCount.value = Number(countData.total).toLocaleString('en-US');
    }

    const currentTopic = getCurrentTopic().toLowerCase();
    const topicSimple = currentTopic.includes('road')
      ? 'roads'
      : currentTopic.includes('building')
        ? 'buildings'
        : prettifyTopic(currentTopic);

    if (countData.total_area !== undefined) {
      const km2 = (countData.total_area / 1_000_000).toLocaleString('en-US', { maximumFractionDigits: 0 });
      totalLength.value = km2;
      tile1Label.value = `km² of ${topicSimple}`;
    } else if (countData.total_length !== undefined) {
      const km = (countData.total_length / 1000).toLocaleString('en-US', { maximumFractionDigits: 0 });
      totalLength.value = km;
      tile1Label.value = `km of ${topicSimple}`;
    } else {
      totalLength.value = '';
      tile1Label.value = '';
    }

    const trace: any = {
      type: 'treemap',
      labels,
      parents: labels.map(() => ''),
      values,
      customdata: values,
      hovertemplate: '%{label}<br>%{customdata:,.0f}' + unitSuffix + ' (%{percentEntry:.1%})<extra></extra>',
      texttemplate: '%{label}<br>%{customdata:,.0f}' + unitSuffix + ' (%{percentEntry:.1%})',
      textinfo: 'label+value+percent entry',
      marker: { colors },
      textfont: { color: textColors }
    };

    const layout = {
      margin: { t: 40, r: 10, l: 10, b: 10 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'Archivo, sans-serif', size: 11 }
    };

    Plotly.react('tag-treemap', [trace], layout, {
      responsive: true,
      displayModeBar: false
    });
  } catch (e) {
    console.error('Failed to load treemap:', e);
  }
}

async function handleIndicatorChange(mapNum: 1 | 2 | 3, indicator: string) {
  if (mapNum === 1) map1Indicator.value = indicator;
  else if (mapNum === 2) map2Indicator.value = indicator;
  else map3Indicator.value = indicator;

  await loadMapData();
}

async function handleGridChange(mapNum: 1 | 2 | 3, layer: string) {
  if (mapNum === 1) map1Layer.value = layer;
  else if (mapNum === 2) map2Layer.value = layer;
  else map3Layer.value = layer;
}

function handleSchoolSwitch(subTopic: string) {
  schoolSubTopic.value = subTopic;
  setCurrentSchoolSubTopic(subTopic);
  loadTreemap();
}

const banner1Style = computed(() => ({
  background: getBannerColor(banner1Level.value),
  color: getBannerTextColor(banner1Level.value)
}));

const banner2Style = computed(() => ({
  background: getBannerColor(banner2Level.value),
  color: getBannerTextColor(banner2Level.value)
}));

const banner3Style = computed(() => ({
  background: getBannerColor(banner3Level.value),
  color: getBannerTextColor(banner3Level.value)
}));

onUnmounted(() => {
  window.removeEventListener('hashchange', handleHashRouting);
});
</script>

<template>
  <div :class="['h-full w-full overflow-hidden flex flex-col relative bg-[#F3F3F3] p-2 gap-2', isEmbed ? 'embed-mode' : '']">
    <ReportHeader
      v-model:selectedCountry="selectedCountry"
      v-model:selectedTopic="selectedTopic"
      :countries="countries"
      :topics="topics"
    />

    <div class="page-content flexible">
      <!-- Normal and embed mode: show full 4-column layout -->
      <template v-if="!isIframe2Col">
        <div class="box-row">
          <div class="grid">
            <div class="box box-centered tile-primary">
              <h3>{{ featureCount }}</h3>
              <h4>Features</h4>
            </div>
            <div class="box box-centered tile-primary">
              <h3>{{ totalLength }}</h3>
              <h4>{{ tile1Label }}</h4>
            </div>
            <div class="box box-centered tile-secondary mobile-hidden" v-if="dataDate">
              <h3>{{ dataDate }}</h3>
              <h4>Last updated</h4>
            </div>
            <div class="box box-centered tile-secondary mobile-hidden" style="padding:0;" v-else></div>
            <div class="box tile-secondary mobile-hidden" style="position: relative;">
              <a href="https://heigit.org" target="_blank" class="heigit-tile-link">
                <img src="https://hot.storage.heigit.org/heigit-hdx-public/oqapi_hdx/logos/heigit-logo.svg" alt="HeiGIT" style="width:160px;height:auto;">
              </a>
            </div>
          </div>
        </div>

        <div class="box-row flexible">
          <div class="grid">
            <div class="image-box" id="tile5">
              <template v-if="showTagCoverage && filteredTagCoverage.length > 0">
                <h4 class="tile-header">Tag Coverage</h4>
                <div class="bar-chart-container">
                  <div class="bar-chart">
                    <div v-for="item in filteredTagCoverage" :key="item.indicator" class="bar-item">
                      <span class="label">{{ item.indicator.replace('attribute-completeness_', '') }}</span>
                      <div class="bar-container">
                        <div class="bar" :style="{ width: `${item.value * 100}%` }"></div>
                      </div>
                      <span class="value">{{ (item.value * 100).toFixed(2) }}%</span>
                    </div>
                  </div>
                </div>
              </template>
              <h4 class="tile-header" style="margin-top: 2rem; margin-bottom: -2rem;">Tag Distribution</h4>
              <div id="school-treemap-switch" :style="{ display: schoolSwitchVisible ? 'flex' : 'none', width: '95%', margin: '3rem auto -2rem auto', justifyContent: 'center', gap: 0, position: 'relative', zIndex: 10 }">
                <button
                  class="switch-btn"
                  :class="{ active: schoolSubTopic === 'operator' }"
                  @click="handleSchoolSwitch('operator')"
                >
                  operator:type
                </button>
                <button
                  class="switch-btn"
                  :class="{ active: schoolSubTopic === 'isced' }"
                  @click="handleSchoolSwitch('isced')"
                >
                  {{ selectedTopic?.toLowerCase().startsWith('hospital') || selectedTopic?.toLowerCase().startsWith('healthcare') ? 'healthcare:speciality' : 'isced:level' }}
                </button>
              </div>
              <div class="plot-container" id="tag-treemap"></div>
            </div>

            <div class="image-box" id="tile6">
              <select
                class="indicator-selector"
                :value="map1Indicator"
                @change="handleIndicatorChange(1, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="ind in indicators" :key="ind" :value="ind">
                  {{ prettifyIndicator(ind) }}
                </option>
              </select>
              <div class="banner" style="padding:0;">
                <div :style="[banner1Style, {
                  padding: '0.25rem 0.75rem',
                  fontWeight: 'bold',
                  fontSize: '0.875rem',
                  borderRadius: 'var(--border-radius)',
                  textAlign: 'center',
                  marginBottom: '0.75rem'
                }]" class="banner-text">
                  {{ banner1Level }} {{ prettifyIndicator(map1Indicator) }}
                </div>
              </div>
              <div class="box" style="position: relative;">
                <MetricMap
                  :key="'road-comparison-map'"
                  containerId="road_comparison_map"
                  :pmtilesUrl="pmtilesUrl"
                  :lookup="map1Lookup"
                  :indicatorName="map1Indicator"
                  :bounds="bounds"
                  :layerName="map1Layer"
                  sourceName="grid_source"
                  :topicId="topicId"
                />
                <select
                  class="grid-selector"
                  :value="map1Layer"
                  @change="handleGridChange(1, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="ADM0">Admin 0</option>
                  <option value="ADM1">Admin 1</option>
                  <option value="h3_hexgrid">Hexagonal Grid</option>
                </select>
                <div class="legend">
                  <div>{{ prettifyIndicator(map1Indicator) }}</div>
                  <span style="background:#F44336;width:10px;height:10px;display:inline-block;"></span> 0–25%<br>
                  <span style="background:#FFEB3B;width:10px;height:10px;display:inline-block;"></span> 25–75%<br>
                  <span style="background:#4CAF50;width:10px;height:10px;display:inline-block;"></span> 75–100%
                </div>
              </div>
              <div class="map-description">{{ map1Description }}</div>
              <div class="plot-container" id="comparison-plot"></div>
            </div>

            <div class="image-box mobile-hidden">
              <select
                class="indicator-selector"
                :value="map2Indicator"
                @change="handleIndicatorChange(2, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="ind in indicators" :key="ind" :value="ind">
                  {{ prettifyIndicator(ind) }}
                </option>
              </select>
              <div class="banner" style="padding:0;">
                <div :style="[banner2Style, {
                  padding: '0.25rem 0.75rem',
                  fontWeight: 'bold',
                  fontSize: '0.875rem',
                  borderRadius: 'var(--border-radius)',
                  textAlign: 'center',
                  marginBottom: '0.75rem'
                }]" class="banner-text">
                  {{ banner2Level }} {{ prettifyIndicator(map2Indicator) }}
                </div>
              </div>
              <div class="box" style="position: relative;">
                <MetricMap
                  :key="'current-map'"
                  containerId="current_map"
                  :pmtilesUrl="pmtilesUrl"
                  :lookup="map2Lookup"
                  :indicatorName="map2Indicator"
                  :bounds="bounds"
                  :layerName="map2Layer"
                  sourceName="grid_source_2"
                  :topicId="topicId"
                />
                <select
                  class="grid-selector"
                  :value="map2Layer"
                  @change="handleGridChange(2, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="ADM0">Admin 0</option>
                  <option value="ADM1">Admin 1</option>
                  <option value="h3_hexgrid">Hexagonal Grid</option>
                </select>
                <div class="legend">
                  <div>{{ prettifyIndicator(map2Indicator) }}</div>
                  <span style="background:#F44336;width:10px;height:10px;display:inline-block;"></span> 0–25%<br>
                  <span style="background:#FFEB3B;width:10px;height:10px;display:inline-block;"></span> 25–75%<br>
                  <span style="background:#4CAF50;width:10px;height:10px;display:inline-block;"></span> 75–100%
                </div>
              </div>
              <div class="map-description">{{ map2Description }}</div>
              <div class="plot-container" id="currentness-plot"></div>
            </div>

            <div class="image-box mobile-hidden">
              <select
                class="indicator-selector"
                :value="map3Indicator"
                @change="handleIndicatorChange(3, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="ind in indicators" :key="ind" :value="ind">
                  {{ prettifyIndicator(ind) }}
                </option>
              </select>
              <div class="banner" style="padding:0;">
                <div :style="[banner3Style, {
                  padding: '0.25rem 0.75rem',
                  fontWeight: 'bold',
                  fontSize: '0.875rem',
                  borderRadius: 'var(--border-radius)',
                  textAlign: 'center',
                  marginBottom: '0.75rem'
                }]" class="banner-text">
                  {{ banner3Level }} {{ prettifyIndicator(map3Indicator) }}
                </div>
              </div>
              <div class="box" style="position: relative;">
                <MetricMap
                  :key="'completeness-map'"
                  containerId="completeness_map"
                  :pmtilesUrl="pmtilesUrl"
                  :lookup="map3Lookup"
                  :indicatorName="map3Indicator"
                  :bounds="bounds"
                  :layerName="map3Layer"
                  sourceName="grid_source_3"
                  :topicId="topicId"
                />
                <select
                  class="grid-selector"
                  :value="map3Layer"
                  @change="handleGridChange(3, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="ADM0">Admin 0</option>
                  <option value="ADM1">Admin 1</option>
                  <option value="h3_hexgrid">Hexagonal Grid</option>
                </select>
                <div class="legend">
                  <div>{{ prettifyIndicator(map3Indicator) }}</div>
                  <span style="background:#F44336;width:10px;height:10px;display:inline-block;"></span> 0–25%<br>
                  <span style="background:#FFEB3B;width:10px;height:10px;display:inline-block;"></span> 25–75%<br>
                  <span style="background:#4CAF50;width:10px;height:10px;display:inline-block;"></span> 75–100%
                </div>
              </div>
              <div class="map-description">{{ map3Description }}</div>
              <div class="plot-container" id="completeness-plot"></div>
            </div>
          </div>
        </div>
      </template>

      <!-- iframe-2col mode: 2-column layout with tile 5 and tile 6 -->
      <template v-else>
        <div class="box-row flexible iframe-2col-grid">
          <div class="grid">
            <div class="image-box" id="tile5">
              <template v-if="showTagCoverage && filteredTagCoverage.length > 0">
                <h4 class="tile-header">Tag Coverage</h4>
                <div class="bar-chart-container">
                  <div class="bar-chart">
                    <div v-for="item in filteredTagCoverage" :key="item.indicator" class="bar-item">
                      <span class="label">{{ item.indicator.replace('attribute-completeness_', '') }}</span>
                      <div class="bar-container">
                        <div class="bar" :style="{ width: `${item.value * 100}%` }"></div>
                      </div>
                      <span class="value">{{ (item.value * 100).toFixed(2) }}%</span>
                    </div>
                  </div>
                </div>
              </template>
              <h4 class="tile-header" style="margin-top: 2rem; margin-bottom: -2rem;">Tag Distribution</h4>
              <div id="school-treemap-switch" :style="{ display: schoolSwitchVisible ? 'flex' : 'none', width: '95%', margin: '3rem auto -2rem auto', justifyContent: 'center', gap: 0, position: 'relative', zIndex: 10 }">
                <button
                  class="switch-btn"
                  :class="{ active: schoolSubTopic === 'operator' }"
                  @click="handleSchoolSwitch('operator')"
                >
                  operator:type
                </button>
                <button
                  class="switch-btn"
                  :class="{ active: schoolSubTopic === 'isced' }"
                  @click="handleSchoolSwitch('isced')"
                >
                  {{ selectedTopic?.toLowerCase().startsWith('hospital') || selectedTopic?.toLowerCase().startsWith('healthcare') ? 'healthcare:speciality' : 'isced:level' }}
                </button>
              </div>
              <div class="plot-container" id="tag-treemap"></div>
            </div>

            <div class="image-box" id="tile6">
              <div class="tile6-split">
                <!-- Left Column: Selector, Banner, Map -->
                <div class="tile6-left">
                  <select
                    class="indicator-selector"
                    :value="map1Indicator"
                    @change="handleIndicatorChange(1, ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="ind in indicators" :key="ind" :value="ind">
                      {{ prettifyIndicator(ind) }}
                    </option>
                  </select>
                  <div class="banner" style="padding:0;">
                    <div :style="[banner1Style, {
                      padding: '0.25rem 0.75rem',
                      fontWeight: 'bold',
                      fontSize: '0.875rem',
                      borderRadius: 'var(--border-radius)',
                      textAlign: 'center',
                      marginBottom: '0.75rem'
                    }]" class="banner-text">
                      {{ banner1Level }} {{ prettifyIndicator(map1Indicator) }}
                    </div>
                  </div>
                  <div class="box" style="position: relative; flex: 1; min-height: 0;">
                    <MetricMap
                      :key="'road-comparison-map'"
                      containerId="road_comparison_map"
                      :pmtilesUrl="pmtilesUrl"
                      :lookup="map1Lookup"
                      :indicatorName="map1Indicator"
                      :bounds="bounds"
                      :layerName="map1Layer"
                      sourceName="grid_source"
                      :topicId="topicId"
                    />
                    <select
                      class="grid-selector"
                      :value="map1Layer"
                      @change="handleGridChange(1, ($event.target as HTMLSelectElement).value)"
                    >
                      <option value="ADM0">Admin 0</option>
                      <option value="ADM1">Admin 1</option>
                      <option value="h3_hexgrid">Hexagonal Grid</option>
                    </select>
                    <div class="legend">
                      <div>{{ prettifyIndicator(map1Indicator) }}</div>
                      <span style="background:#F44336;width:10px;height:10px;display:inline-block;"></span> 0–25%<br>
                      <span style="background:#FFEB3B;width:10px;height:10px;display:inline-block;"></span> 25–75%<br>
                      <span style="background:#4CAF50;width:10px;height:10px;display:inline-block;"></span> 75–100%
                    </div>
                  </div>
                </div>
                <!-- Right Column: Description, Plot -->
                <div class="tile6-right">
                  <div class="map-description">{{ map1Description }}</div>
                  <div class="plot-container" id="comparison-plot"></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <ReportFooter :parquetDate="dataDate" />
  </div>
</template>

<style scoped>
.page-content.flexible {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.embed-mode .page-content.flexible {
  overflow-y: auto;
}

.box-row {
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--border-radius);
  padding: var(--spacing);
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.05);
}

.box-row.flexible {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing);
  height: 100%;
}

.box-row.flexible .grid {
  flex: 1;
  min-height: 0;
  flex-wrap: nowrap;
}

.box,
.image-box {
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--border-radius);
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.05);
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.image-box .box {
  flex: 1;
  min-height: 0;
}

.box-centered {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.box h3 {
  font-size: 2rem !important;
  font-weight: 700 !important;
  margin: 0;
  margin-top: 0.25rem;
  margin-bottom: 0rem;
  min-height: 2.4rem;
}

.box h4 {
  font-weight: 700 !important;
  margin: 0.25rem 0;
  margin-top: 0rem;
  margin-bottom: 0.25rem;
  font-size: 1rem !important;
  min-height: 1.2rem;
}

.tile-secondary h3 {
  font-size: 1.25rem !important;
  font-weight: 600 !important;
  color: #2C3038 !important;
  min-height: auto !important;
  margin-top: 0.5rem !important;
}

.tile-secondary h4 {
  font-size: 0.8rem !important;
  font-weight: 400 !important;
  color: #888 !important;
  min-height: auto !important;
  margin-bottom: 0.5rem !important;
}

.heigit-tile-link {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}

.heigit-tile-link:hover {
  background: #f3f3f3;
}

.heigit-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}

.heigit-logo img {
  display: block;
  width: 100%;
  height: auto;
  object-fit: contain;
}

.tile-header {
  margin-top: 1rem;
  text-align: center;
  font-weight: 800;
  font-size: 1rem;
  margin-bottom: 1rem;
}

.map-container {
  flex: 1 1 0;
  min-height: 150px;
  width: 100%;
  border-radius: var(--border-radius);
  overflow: hidden;
  position: relative;
}

.legend {
  position: absolute;
  bottom: 2px;
  left: 2px;
  background: rgba(255, 255, 255, 0.9);
  padding: 5px 6px;
  border-radius: 6px;
  border: 1px solid #aaa;
  font-size: 0.5rem;
  line-height: 1.2;
  z-index: 2;
}

.legend-title {
  font-weight: bold;
  margin-bottom: 2px;
}

.map-description {
  border: 1px solid var(--color-border);
  border-radius: var(--border-radius);
  background: var(--color-card-bg);
  padding: 0.6rem;
  font-size: 0.75rem;
  line-height: 1.3;
  margin-top: 0.5rem;
  overflow-wrap: break-word;
  max-height: none;
  width: 95%;
  margin-left: auto;
  margin-right: auto;
  box-sizing: border-box;
}

.banner {
  padding: 0;
}

.indicator-selector {
  font-size: 0.8rem;
  padding: 0.25rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #aaa;
  background: rgba(255, 255, 255, 0.9);
  margin-bottom: 0.5rem;
  margin-top: 0.5rem;
}

.image-box .plot-container {
  flex: 1 1 0;
  min-height: 0;
  width: 95%;
  margin-left: auto;
  margin-right: auto;
}

.plot-container > div {
  width: 100% !important;
  height: 100% !important;
  min-height: 150px;
}

#tile5 .plot-container {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  display: flex;
  overflow: hidden;
}

#tag-treemap {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  width: 100% !important;
}

#tag-treemap > div {
  width: 100% !important;
  height: 100% !important;
}

.bar-chart-container {
  width: 95%;
  margin-left: auto;
  margin-right: auto;
}

.box-row.flexible .image-box:not(#tile5) > *,
.box-row.flexible .image-box:not(#tile5) .box,
.box-row.flexible .image-box:not(#tile5) .map-description,
.box-row.flexible .image-box:not(#tile5) .plot-container {
  width: 95%;
  margin-left: auto;
  margin-right: auto;
}

.bar-chart {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.bar-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.bar-item .label {
  width: 60px;
  font-size: 0.75rem;
}

.bar-container {
  flex: 1;
  background: #e1e0e1;
  border-radius: 4px;
  overflow: hidden;
  height: 12px;
  position: relative;
}

.bar-container .bar {
  height: 100%;
  background: #4CAF50;
  text-align: right;
  font-size: 0.65rem;
  color: white;
  line-height: 12px;
  padding-right: 2px;
}

.bar-item .value {
  width: 50px;
  font-size: 0.75rem;
  text-align: right;
}

.grid-selector {
  position: absolute;
  top: 4px;
  left: 4px;
  z-index: 3;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #aaa;
  border-radius: 6px;
  font-size: 0.7rem;
  padding: 2px 4px;
}

.switch-btn {
  flex: 1;
  font-family: var(--font-sans);
  font-size: 0.75rem;
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--color-border);
  background: #fafafa;
  cursor: pointer;
  color: var(--color-text);
  transition: all 0.2s ease;
  font-weight: 400;
}

.switch-btn:first-child {
  border-radius: 6px 0 0 6px;
  border-right: none;
}

.switch-btn:last-child {
  border-radius: 0 6px 6px 0;
}

.switch-btn.active {
  background: #eee;
  border-color: #aaa;
  font-weight: 800;
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
}

.switch-btn:hover:not(.active) {
  background: #f0f0f0;
}

@media (max-width: 768px) {
  .page-content.flexible {
    overflow-y: auto;
    overflow-x: hidden;
  }

  .box-row {
    min-width: 0;
    flex: none;
  }

  .grid {
    flex-direction: column;
    height: auto;
  }

  .box-row.flexible .grid {
    flex-direction: column;
    height: auto;
  }

  .box-row:not(.flexible) .grid {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .box-row:not(.flexible) .box {
    min-height: 60px;
    flex: none;
  }

  .box,
  .image-box {
    min-width: 280px;
    min-height: auto;
    overflow: visible;
  }

  .image-box {
    display: flex;
    flex-direction: column;
  }

  .image-box > .box {
    height: 350px;
    min-height: 350px;
    flex: none;
  }

  .map-container {
    min-height: 350px;
    height: 350px;
    width: 100%;
    flex: none;
    overflow: hidden;
  }

  .map-container > div {
    width: 100% !important;
    height: 350px !important;
  }

  .box h3 {
    font-size: 1.25rem !important;
    min-height: 1.5rem;
    margin-top: 0.25rem;
  }

  .box h4 {
    font-size: 0.75rem !important;
    min-height: 1rem;
    margin: 0.25rem 0;
  }

  .tile-header {
    font-size: 0.875rem !important;
  }

  .box-row.flexible {
    flex: none;
    min-height: auto;
  }

  #tag-treemap {
    height: 300px !important;
    min-height: 300px !important;
  }

  #tag-treemap > div {
    height: 300px !important;
    min-height: 300px !important;
  }

  .plot-container {
    height: 250px !important;
    min-height: 250px !important;
    flex: none;
  }

  .plot-container > div {
    height: 250px !important;
    min-height: 250px !important;
  }

  .map-description {
    min-height: 50px;
  }

  .mobile-hidden {
    display: none !important;
  }

  #tile5 {
    order: 1;
  }

  .box-row.flexible .image-box:not(#tile5) {
    order: 0;
  }
}

/* Embed mode styles for HDX integration - compact multi-column layout */
.embed-mode {
  padding: 0.25rem !important;
  gap: 0.25rem !important;
}

/* Keep multi-column layout but make it compact */
.embed-mode .box-row.flexible .grid {
  flex-wrap: nowrap !important;
  height: auto !important;
  gap: 0.25rem !important;
}

.embed-mode .image-box {
  flex: 1 1 0 !important;
  min-width: 0 !important;
}

/* Smaller header in embed mode */
.embed-mode header {
  padding: 0.1rem 0.3rem !important;
  gap: 0.1rem !important;
}

.embed-mode .header-title {
  font-size: 0.9rem !important;
  margin-left: 0 !important;
}

.embed-mode .title-logo {
  height: 20px !important;
}

.embed-mode .header-selectors {
  gap: 0.15rem !important;
}

.embed-mode .selector-group.horizontal label {
  font-size: 0.7rem !important;
}

.embed-mode .country-select {
  font-size: 0.7rem !important;
  padding: 0.15rem 0.25rem !important;
}

/* Smaller footer in embed mode */
.embed-mode footer {
  padding: 0.1rem 0.3rem !important;
  font-size: 0.6rem !important;
  gap: 0.25rem !important;
}

.embed-mode .footer-btn {
  padding: 0.1rem 0.2rem !important;
  font-size: 0.6rem !important;
}

.embed-mode .ohsome-link img {
  height: 15px !important;
}

/* Compact tiles */
.embed-mode .box h3 {
  font-size: 1rem !important;
  min-height: 1.2rem !important;
  margin-top: 0.15rem !important;
  margin-bottom: 0.1rem !important;
}

.embed-mode .box h4 {
  font-size: 0.65rem !important;
  min-height: 0.8rem !important;
  margin: 0.1rem 0 !important;
}

.embed-mode .tile-secondary h3 {
  font-size: 0.8rem !important;
}

/* Smaller maps - ensure they're visible */
.embed-mode .map-container {
  min-height: 140px !important;
  flex: 1 1 140px !important;
}

.embed-mode .image-box > .box {
  min-height: 150px !important;
  flex: 1 1 150px !important;
}

/* Smaller plots */
.embed-mode .image-box .plot-container {
  min-height: 100px !important;
  flex: 1 1 100px !important;
}

.embed-mode .plot-container > div {
  min-height: 100px !important;
}

/* Compact text elements */
.embed-mode .tile-header {
  font-size: 0.7rem !important;
  margin-top: 0.2rem !important;
  margin-bottom: 0.2rem !important;
}

.embed-mode .map-description {
  font-size: 0.55rem !important;
  padding: 0.2rem !important;
  margin-top: 0.15rem !important;
  line-height: 1.1 !important;
}

.embed-mode .indicator-selector,
.embed-mode .grid-selector {
  font-size: 0.55rem !important;
  padding: 0.1rem 0.2rem !important;
  margin-bottom: 0.2rem !important;
  margin-top: 0.2rem !important;
}

.embed-mode .legend {
  font-size: 0.35rem !important;
  padding: 1px 2px !important;
  line-height: 1 !important;
}

.embed-mode .bar-item .label,
.embed-mode .bar-item .value {
  font-size: 0.55rem !important;
}

.embed-mode .bar-container {
  height: 6px !important;
}

.embed-mode .bar-chart {
  gap: 0.2rem !important;
  margin-top: 0.2rem !important;
}

.embed-mode .switch-btn {
  font-size: 0.55rem !important;
  padding: 0.15rem 0.25rem !important;
}

/* Make treemap tile header more compact */
.embed-mode #tile5 .tile-header {
  margin-top: 0.2rem !important;
  margin-bottom: 0.2rem !important;
}

/* Reduce margin for school switch in embed mode */
.embed-mode #school-treemap-switch {
  margin: 1.5rem auto -1rem auto !important;
}

.embed-mode #tag-treemap {
  min-height: 150px !important;
  flex: 1 1 150px !important;
}

/* Make page content fit without scrolling in embed mode */
.embed-mode .page-content.flexible {
  overflow: hidden !important;
  flex: 1 1 auto !important;
}

/* Make stat boxes row more compact in embed mode */
.embed-mode .box-row:not(.flexible) {
  padding: 0.1rem !important;
}

.embed-mode .box-row:not(.flexible) .box h3 {
  font-size: 0.8rem !important;
  min-height: 1rem !important;
  margin-top: 0.1rem !important;
  margin-bottom: 0 !important;
}

.embed-mode .box-row:not(.flexible) .box h4 {
  font-size: 0.6rem !important;
  min-height: 0.7rem !important;
  margin: 0.05rem 0 !important;
}

/* Ensure tile 5 is visible */
.embed-mode #tile5 {
  min-height: 160px !important;
}

/* Smaller HeiGIT logo in embed mode */
.embed-mode .heigit-tile-link img {
  width: 100px !important;
  height: auto !important;
}

/* Smaller banner text in embed mode */
.embed-mode .banner-text {
  font-size: 0.65rem !important;
  padding: 0.1rem 0.4rem !important;
}

.embed-mode .banner {
  margin-bottom: 0.25rem !important;
}

/* Very narrow embed containers - stack everything vertically */
@media (max-width: 600px) {
  .embed-mode .box-row.flexible .grid {
    flex-direction: column !important;
    flex-wrap: wrap !important;
  }
  
  .embed-mode .image-box {
    width: 100% !important;
    min-width: unset !important;
  }
  
  .embed-mode .map-container {
    min-height: 250px !important;
  }
  
  .embed-mode .plot-container {
    min-height: 180px !important;
  }
}

/* iframe-2col mode: 2-column layout with tile 5 and tile 6 */
.iframe-2col-grid .grid {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  gap: 0.25rem !important;
  height: 100% !important;
}

.iframe-2col-grid .grid > #tile5 {
  flex: 1 1 0 !important; /* 1/3 width */
  min-width: 0 !important;
}

.iframe-2col-grid .grid > #tile6 {
  flex: 2 1 0 !important; /* 2/3 width */
  min-width: 0 !important;
  display: flex !important;
  flex-direction: column !important;
  overflow: hidden !important;
}

.iframe-2col-grid .image-box > .box {
  min-height: 150px !important;
  flex: 1 1 150px !important;
}

.iframe-2col-grid #tag-treemap {
  min-height: 150px !important;
  flex: 1 1 150px !important;
  margin-top: -2rem !important;

}

.iframe-2col-grid .plot-container {
  min-height: 100px !important;
  flex: 1 1 100px !important;
}

.iframe-2col-grid .plot-container > div {
  min-height: 100px !important;
}

.iframe-2col-grid .map-description {
  font-size: 0.55rem !important;
  padding: 0.2rem !important;
  margin-top: 0.15rem !important;
  line-height: 1.1 !important;
}

.iframe-2col-grid .tile-header {
  font-size: 0.85rem !important;
  margin-top: 2rem !important;
  margin-bottom: -2rem !important;
}

.iframe-2col-grid .indicator-selector,
.iframe-2col-grid .grid-selector {
  font-size: 0.7rem !important;
  padding: 0.15rem 0.3rem !important;
  margin-bottom: 0.3rem !important;
  margin-top: 0.3rem !important;
}

.iframe-2col-grid .legend {
  font-size: 0.5rem !important;
  padding: 2px 4px !important;
  line-height: 1.1 !important;
}

.iframe-2col-grid .bar-item .label,
.iframe-2col-grid .bar-item .value {
  font-size: 0.7rem !important;
}

.iframe-2col-grid .bar-container {
  height: 6px !important;
}

.iframe-2col-grid .bar-chart {
  gap: 0.2rem !important;
  margin-top: 0.2rem !important;
  margin-bottom: 2rem !important;
}

.iframe-2col-grid .switch-btn {
  font-size: 0.7rem !important;
  padding: 0.2rem 0.3rem !important;
}

.iframe-2col-grid .banner-text {
  font-size: 0.75rem !important;
  padding: 0.15rem 0.5rem !important;
}

.iframe-2col-grid .banner {
  margin-bottom: 0.25rem !important;
}

/* Tile 6 split layout: left (selector/banner/map) and right (description/plot) */
.iframe-2col-grid .tile6-split {
  display: flex !important;
  flex-direction: row !important;
  gap: 0.25rem !important;
  flex: 1 1 auto !important;
  min-height: 0 !important;
  height: 100% !important;
  overflow: hidden !important;
}

.iframe-2col-grid .tile6-left {
  flex: 1 1 50% !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 0.25rem !important;
  min-height: 0 !important;
  overflow: hidden !important;
  margin-top: 0.5rem !important;
}

.iframe-2col-grid .tile6-right {
  flex: 1 1 50% !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 0.25rem !important;
  min-height: 0 !important;
  overflow: hidden !important;
  margin-top: 0.5rem !important;
}

.iframe-2col-grid .tile6-left .box {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow: hidden !important;
  margin-bottom: 1rem !important;
  width: 100% !important;
}

.iframe-2col-grid .tile6-left .box > div:first-child {
  height: 100% !important;
  width: 100% !important;
}

.iframe-2col-grid .tile6-right .map-description {
  flex: 0 0 auto !important;
  max-height: 45% !important;
  overflow-y: auto !important;
  font-size: 0.75rem !important;
  padding: 0.3rem !important;
  line-height: 1.3 !important;
}

.iframe-2col-grid .tile6-right .plot-container {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow: hidden !important;
}

/* Ensure iframe-2col mode fits well in iframe */
.iframe-2col-grid {
  overflow: hidden !important;
  flex: 1 1 auto !important;
}

/* Ensure iframe-2col mode fits well in iframe */
.iframe-2col-grid {
  overflow: hidden !important;
  flex:1 1 auto !important;
}

/* Hide ohsome dashboard button in iframe modes */
.embed-mode :deep(.footer-center),
.embed-mode :deep(.ohsome-link) {
  display: none !important;
}

/* Reduce footer height in iframe modes */
.embed-mode :deep(footer) {
  padding: 0.15rem 0.75rem !important;
  gap: 0.25rem !important;
  font-size: 0.7rem !important;
}

.embed-mode :deep(.footer-btn) {
  padding: 0.25rem 0.4rem !important;
  font-size: 0.65rem !important;
}

.embed-mode :deep(.footer-right) {
  font-size: 0.65rem !important;
}
</style>
