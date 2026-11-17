%% 青山卫士 · 生态警务风险模型（MATLAB R2025b）
% 输入：data/hotspots_input.csv
% 输出：data/model_weights.json、data/hotspots_scored.csv、data/matlab_summary.json
%       web/assets/matlab_weights.png、matlab_levels.png、matlab_top10.png、matlab_map.png
%
% 方法：AHP 层次分析法求 8 个风险因子权重 + 加权综合评分
% 因子：类别严重度、异常数量、累计面积、复发性、地形、可达性、当前环境、数据置信度

clc; clear; close all;

root = fileparts(fileparts(mfilename('fullpath')));
dataDir = fullfile(root, 'data');
assetDir = fullfile(root, 'web', 'assets');
if ~exist(assetDir, 'dir'); mkdir(assetDir); end

inCsv = fullfile(dataDir, 'hotspots_input.csv');
if ~exist(inCsv, 'file')
    error('缺少 %s，请先运行 app/risk.py 生成高发点输入。', inCsv);
end

set(0, 'DefaultAxesFontName', 'Microsoft YaHei');
set(0, 'DefaultTextFontName', 'Microsoft YaHei');
set(0, 'DefaultAxesFontSize', 10);
dark = [0.043 0.075 0.110];
panel = [0.059 0.094 0.137];
txt = [0.906 0.949 0.988];
muted = [0.520 0.588 0.655];

%% 1. AHP 判断矩阵（8 个因子）
% 顺序：severity, count, area, recurrence, terrain, access, environment, confidence
A = [
    1    2    2    2    3    3    3    7;
    1/2  1    1    1    2    2    2    5;
    1/2  1    1    1    2    2    2    5;
    1/2  1    1    1    2    2    2    5;
    1/3  1/2  1/2  1/2  1    1    1    3;
    1/3  1/2  1/2  1/2  1    1    1    3;
    1/3  1/2  1/2  1/2  1    1    1    3;
    1/7  1/5  1/5  1/5  1/3  1/3  1/3  1
];
factorNames = {'类别严重度','异常数量','累计面积','复发性','地形','可达性','当前环境','数据置信度'};
factorKeys  = {'severity','count','area','recurrence','terrain','access','environment','confidence'};
n = numel(factorKeys);
[V, D] = eig(A);
[lambda, idx] = max(real(diag(D)));
w = abs(real(V(:, idx)));
w = w / sum(w);
CI = (lambda - n) / (n - 1);
RI = 1.41;                 % n=8 的随机一致性指标
CR = CI / RI;
fprintf('AHP：lambda=%.4f，CI=%.4f，CR=%.4f %s\n', lambda, CI, CR, ternary(CR < 0.1, '(通过)', '(需检查)'));

W = struct();
for i = 1:n
    W.(factorKeys{i}) = w(i);
end
weightsJson = struct('weights', W, 'consistency_ratio', CR, 'lambda_max', lambda, ...
    'method', 'AHP', 'engine', 'MATLAB R2025b', ...
    'generated_at', datestr(now, 'yyyy-mm-ddTHH:MM:SS'));
write_json(fullfile(dataDir, 'model_weights.json'), weightsJson);

%% 2. 读取高发点并计算风险分
T = readtable(inCsv, 'Delimiter', ',', 'TextType', 'string', 'Encoding', 'UTF-8');
m = height(T);
if m == 0
    error('高发点输入为空。');
end

count_n = min(T.count / 6, 1);
area_n = min(T.area_ha / 800, 1);
recurrence_n = min(T.recurrence / 4, 1);
terrain_n = min(max(T.slope_deg / 35, 0), 1);
exposure = max(min(max(1 - T.dist_place_km / 25, 0), 1), min(max(1 - T.dist_road_km / 15, 0), 1));
patrol = min(max(T.dist_police_km / 40, 0), 1);
access_n = 0.5 * exposure + 0.5 * patrol;

fire_wx = T.fire_wx;
wet = T.wet;
dry = T.dry;
monthNow = month(datetime('now'));
winter = double(ismember(monthNow, [11 12 1 2 3]));

env_n = zeros(m, 1);
for i = 1:m
    cls = string(T.dominant_class(i));
    if cls == "火烧迹地"
        env_n(i) = 0.7 * fire_wx(i) + 0.3 * dry(i);
    elseif cls == "盗采痕迹"
        env_n(i) = 0.6 * dry(i) + 0.2 * fire_wx(i) + 0.2 * terrain_n(i);
    elseif cls == "滑坡" || cls == "泥石流"
        env_n(i) = 0.7 * wet(i) + 0.3 * terrain_n(i);
    elseif cls == "雪线变化"
        env_n(i) = winter;
    else
        env_n(i) = 0.5 * fire_wx(i) + 0.5 * wet(i);
    end
end
env_n = min(max(env_n, 0), 1);
severity_n = min(max(T.severity, 0), 1);
confidence_n = min(max(T.confidence, 0), 1);

F = [severity_n, count_n, area_n, recurrence_n, terrain_n, access_n, env_n, confidence_n];
risk = 100 * (F * w);
risk = min(max(risk, 0), 100);

risk_level = strings(m, 1);
risk_level(risk >= 70) = "red";
risk_level(risk >= 60 & risk < 70) = "orange";
risk_level(risk >= 50 & risk < 60) = "yellow";
risk_level(risk < 50) = "blue";

out = table(T.id, risk, risk_level, severity_n, count_n, area_n, recurrence_n, ...
    terrain_n, access_n, env_n, confidence_n, ...
    'VariableNames', {'id','risk_score','risk_level','severity_n','count_n','area_n', ...
    'recurrence_n','terrain_n','access_n','environment_n','confidence_n'});
writetable(out, fullfile(dataDir, 'hotspots_scored.csv'), 'Encoding', 'UTF-8');

%% 3. 汇总
levelNames = ["red","orange","yellow","blue"];
levelCounts = zeros(4, 1);
for i = 1:4
    levelCounts(i) = sum(risk_level == levelNames(i));
end
[riskSorted, order] = sort(risk, 'descend');
topN = min(5, m);
top = struct('id', {}, 'name', {}, 'risk_score', {}, 'risk_level', {});
for i = 1:topN
    j = order(i);
    top(i).id = string(T.id(j));
    top(i).name = string(T.name(j));
    top(i).risk_score = risk(j);
    top(i).risk_level = risk_level(j);
end
summary = struct('method', 'AHP + weighted sum', 'engine', 'MATLAB R2025b', ...
    'consistency_ratio', CR, 'generated_at', datestr(now, 'yyyy-mm-ddTHH:MM:SS'), ...
    'level_counts', struct('red', levelCounts(1), 'orange', levelCounts(2), ...
    'yellow', levelCounts(3), 'blue', levelCounts(4)), 'top5', top);
write_json(fullfile(dataDir, 'matlab_summary.json'), summary);

%% 4. 权重图
fig = figure('Visible','off','Color',dark,'Position',[0 0 760 420]);
ax = axes(fig,'Color',panel,'XColor',muted,'YColor',txt,'FontSize',10);
[ws, ord] = sort(w, 'descend');
b = barh(ax, ws, 'FaceColor','flat','EdgeColor','none');
b.CData = repmat([0.223 0.741 0.973], numel(ws), 1);
set(ax,'YDir','reverse');
ax.YTick = 1:numel(ws);
ax.YTickLabel = factorNames(ord);
for i = 1:numel(ws)
    text(ax, ws(i) + 0.006, i, sprintf('%.1f%%', ws(i)*100), 'Color', txt, ...
        'FontSize', 9, 'VerticalAlignment','middle');
end
xlim(ax, [0 max(0.36, max(ws)*1.25)]);
grid(ax,'on'); ax.GridColor = [0.10 0.16 0.23]; ax.GridAlpha = 0.8;
title(ax, 'AHP 风险因子权重（MATLAB R2025b）','Color',txt,'FontWeight','bold','FontSize',13);
xlabel(ax, '权重','Color',muted);
exportgraphics(fig, fullfile(assetDir,'matlab_weights.png'), 'Resolution', 180, 'BackgroundColor', dark);
close(fig);

%% 5. 等级分布图
fig = figure('Visible','off','Color',dark,'Position',[0 0 620 360]);
ax = axes(fig,'Color',panel,'XColor',muted,'YColor',txt,'FontSize',10);
colors = [1.000 0.231 0.188; 1.000 0.584 0.000; 1.000 0.839 0.039; 0.039 0.518 1.000];
b = bar(ax, levelCounts, 'FaceColor','flat','EdgeColor','none');
b.CData = colors;
ax.XTick = 1:4; ax.XTickLabel = {'红色','橙色','黄色','蓝色'};
ylabel(ax, '高发点数量','Color',muted);
title(ax, '危险等级分布','Color',txt,'FontWeight','bold','FontSize',13);
grid(ax,'on'); ax.GridColor = [0.10 0.16 0.23]; ax.GridAlpha = 0.8;
for i = 1:4
    text(ax, i, levelCounts(i)+0.15, num2str(levelCounts(i)), 'Color', txt, ...
        'HorizontalAlignment','center','FontSize',11,'FontWeight','bold');
end
exportgraphics(fig, fullfile(assetDir,'matlab_levels.png'), 'Resolution', 180, 'BackgroundColor', dark);
close(fig);

%% 6. Top 10 风险图
k = min(10, m);
fig = figure('Visible','off','Color',dark,'Position',[0 0 820 460]);
ax = axes(fig,'Color',panel,'XColor',muted,'YColor',txt,'FontSize',9);
topRisk = riskSorted(1:k);
topNames = strings(k,1);
topColors = zeros(k,3);
for i = 1:k
    j = order(i);
    nm = string(T.name(j));
    if strlength(nm) > 14; nm = extractBefore(nm, 15) + "…"; end
    topNames(i) = nm;
    c = risk_level(j);
    if c == "red"; topColors(i,:) = colors(1,:);
    elseif c == "orange"; topColors(i,:) = colors(2,:);
    elseif c == "yellow"; topColors(i,:) = colors(3,:);
    else; topColors(i,:) = colors(4,:); end
end
b = barh(ax, topRisk, 'FaceColor','flat','EdgeColor','none');
b.CData = topColors;
set(ax,'YDir','reverse');
ax.YTick = 1:k; ax.YTickLabel = topNames;
xlim(ax, [0 105]);
for i = 1:k
    text(ax, topRisk(i)+1.2, i, sprintf('%.1f', topRisk(i)), 'Color', txt, ...
        'FontSize', 9, 'VerticalAlignment','middle');
end
grid(ax,'on'); ax.GridColor = [0.10 0.16 0.23]; ax.GridAlpha = 0.8;
title(ax, '高发点风险 Top 10','Color',txt,'FontWeight','bold','FontSize',13);
xlabel(ax, '风险分（0-100）','Color',muted);
exportgraphics(fig, fullfile(assetDir,'matlab_top10.png'), 'Resolution', 180, 'BackgroundColor', dark);
close(fig);

%% 7. 风险气泡图（经纬度）
fig = figure('Visible','off','Color',dark,'Position',[0 0 860 460]);
ax = geoaxes(fig,'Color',panel);
geobasemap(ax,'none');
hold(ax,'on');
geoscatter(ax, T.lat, T.lon, 40 + risk, risk, 'filled', 'MarkerEdgeColor', [1 1 1]*0.0);
colormap(ax, turbo(256));
c = colorbar(ax); c.Color = txt; c.Label.String = '风险分'; c.Label.Color = muted;
geolimits(ax, [39.9 41.6], [108.9 112.8]);
title(ax, '高发点风险气泡图（大小=风险，颜色=风险）','Color',txt,'FontWeight','bold','FontSize',13);
grid(ax,'on');
exportgraphics(fig, fullfile(assetDir,'matlab_map.png'), 'Resolution', 180, 'BackgroundColor', dark);
close(fig);

fprintf('MATLAB 风险模型完成：%d 个高发点，CR=%.4f\n', m, CR);
fprintf('输出：data/model_weights.json, data/hotspots_scored.csv, data/matlab_summary.json\n');
fprintf('图表：web/assets/matlab_weights.png, matlab_levels.png, matlab_top10.png, matlab_map.png\n');

%% 辅助函数
function write_json(path, s)
    fid = fopen(path, 'w', 'n', 'UTF-8');
    if fid < 0; error('无法写入 %s', path); end
    fwrite(fid, jsonencode(s), 'char');
    fclose(fid);
end

function out = ternary(cond, a, b)
    if cond; out = a; else; out = b; end
end
