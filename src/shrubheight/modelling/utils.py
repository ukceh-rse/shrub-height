# -*- coding: utf-8 -*-
"""
Created on Wed Nov 29 06:55:46 2023

@author: RafBar
"""

import time
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.cluster import hierarchy
from scipy.spatial.distance import squareform
from scipy.stats import loguniform, randint
from sklearn.decomposition import PCA
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import KFold, RandomizedSearchCV
from sklearn.neighbors import KNeighborsRegressor
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeRegressor
from statsmodels.stats.outliers_influence import variance_inflation_factor
import logging


def fs_hcluster(df, features, target, cluster_threshold=0.4, plot=False):
    """
    Perform feature selection based on hierarchical clustering and correlation analysis.

    Parameters:
    df (pd.DataFrame): The input DataFrame containing features and target.
    target (str): The name of the target variable.
    cluster_threshold (float): Threshold for forming flat clusters.

    Returns:
    cluster_feature (dict): Dictionaire containing the cluster with their respective features.
    selected_features (list): The selected feature for model running.
    """
    # Compute the correlation matrix
    dfcorr = df.corr(method="spearman").abs()
    corr = dfcorr.loc[features, features].values
    corr = np.nan_to_num(corr)

    # Ensure the correlation matrix is symmetric
    corr = (corr + corr.T) / 2
    np.fill_diagonal(corr, 1)

    # Convert the correlation matrix to a distance matrix
    distance_matrix = 1 - np.abs(corr)
    dist_linkage = hierarchy.ward(squareform(distance_matrix))

    # Hierarchical clustering
    cluster_ids = hierarchy.fcluster(
        dist_linkage, cluster_threshold, criterion="distance"
    )
    cluster_id_to_feature_ids = defaultdict(list)
    cluster_feature = defaultdict(list)
    for idx, cluster_id in enumerate(cluster_ids):
        cluster_id_to_feature_ids[cluster_id].append(idx)
        cluster_feature["Cluster " + str(cluster_id)].append(df.columns[idx])

    # Select features with the greatest correlation to the target
    selected_features = []
    for v in cluster_id_to_feature_ids.values():
        targetcorr = dfcorr[target].iloc[v].max()
        bestfeat = dfcorr.reset_index().index[dfcorr[target] == targetcorr].values[0]
        selected_features.append(df.columns[bestfeat])

    # Plot the correlation matrix and the dendogram
    if plot:
        fig, ax = plt.subplots(1, 1, figsize=(6, 6), dpi=300)
        dendro = hierarchy.dendrogram(
            dist_linkage, labels=features, ax=ax, leaf_rotation=90
        )
        dendro_idx = np.arange(0, len(dendro["ivl"]))
        fig.tight_layout()
        plt.show()

        fig, ax = plt.subplots(1, 1, figsize=(9, 9), dpi=300)
        ax.imshow(corr[dendro["leaves"], :][:, dendro["leaves"]])
        ax.set_xticks(dendro_idx)
        ax.set_yticks(dendro_idx)
        ax.set_xticklabels(dendro["ivl"], rotation="vertical")
        ax.set_yticklabels(dendro["ivl"])
        fig.tight_layout()
        plt.show()

        fig, ax = plt.subplots(1, 1, figsize=(15, 12), dpi=300)
        sns.heatmap(abs(dfcorr), ax=ax, cmap="flare")
        fig.tight_layout()
        plt.show()

    return cluster_feature, selected_features


def pca_cluster_transform(df, cluster_feature):

    X = MinMaxScaler().fit_transform(df.values)

    pcs = {}

    for cluster, features in cluster_feature.items():
        pca = PCA(n_components=1)  # Only the first principal component
        pca.fit(X[:, features])
        pcs[cluster] = pca.transform(X[:, features])

    feature_matrix = np.hstack([pcs[cluster] for cluster in sorted(pcs.keys())])

    return feature_matrix


def calculate_vif(df, thresh=10):
    Xv = StandardScaler().fit_transform(df.values)  # FOR THE VIF
    dfi = pd.DataFrame(Xv, columns=df.columns)

    variables = list(range(dfi.shape[1]))
    dropped = True
    while dropped:
        dropped = False
        vif = [
            variance_inflation_factor(dfi.iloc[:, variables].values, ix)
            for ix in range(dfi.iloc[:, variables].shape[1])
        ]
        maxloc = vif.index(max(vif))
        if max(vif) > thresh:
            print(
                "dropping '"
                + dfi.iloc[:, variables].columns[maxloc]
                + "' at index: "
                + str(maxloc)
                + "| with value: "
                + str(max(vif))
            )
            del variables[maxloc]
            dropped = True

    print("Remaining variables:")
    print(dfi.columns[variables])

    return dfi.columns[variables]


def kfold_cv(X, y, model, grid):
    """
    Perform k-fold cross-validation on a given dataset using a specified model and parameter grid.

    Parameters:
    - X (pd.DataFrame or np.ndarray): Input features.
    - y (pd.Series or np.ndarray): Target variable.
    - model: Machine learning model to be trained.
    - grid (dict): Hyperparameter grid for tuning the model.

    Returns:
    - result (pd.DataFrame): DataFrame with observed values, predicted values.
    - imps (pd.DataFrame): DataFrame with feature importances.
    """
    n_splits = 10
    cv = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    yhat = np.empty(y.size, dtype=y.dtype)

    # Create DataFrame for importance scores - 10 repeats per fold
    n_features = X.shape[1]
    n_importance_rows = n_splits * 10  # 10 repeats per fold
    imps = pd.DataFrame(
        columns=range(1, n_features + 1), index=range(n_importance_rows)
    )

    # Track current row in importance scores DataFrame
    importance_row = 0

    for train_index, test_index in cv.split(X, y):
        y_pred, r = train_test(X, y, train_index, test_index, grid, model)
        yhat[test_index] = y_pred

        # Store importance scores for this fold (10 rows)
        imps.iloc[importance_row : importance_row + 10, :] = (  # noqa: E203
            r.importances.T
        )
        importance_row += 10

        fold_number = importance_row // 10
        progress = fold_number * 100 / n_splits
        print(f"Processing: {progress:.0f}%")

    return yhat, imps


def train_test(X, y, train_index, test_index, grid, model):
    X_train, X_test = X[train_index], X[test_index]
    y_train, y_test = y[train_index], y[test_index]

    randomSearch = RandomizedSearchCV(
        estimator=model,
        n_jobs=-1,
        n_iter=100,
        cv=5,
        param_distributions=grid,
        scoring="r2",
    )  # change to r2 for regression
    searchResults = randomSearch.fit(X_train, y_train)

    print("\n")
    print("Best score: " + str(randomSearch.best_score_))
    print("Best params: " + str(randomSearch.best_params_) + "\n")

    model_opt = searchResults.best_estimator_

    model_opt.fit(X_train, y_train)
    y_pred = model_opt.predict(X_test)

    try:
        r = permutation_importance(
            model_opt, X_test, y_test, n_repeats=10, random_state=0, scoring="r2"
        )
    except Exception as e:
        logging.warning(f"Could not compute permutation importance: {str(e)}")
        r = 0

    return y_pred, r


def model_run(X, y, mlmodel, method="kfold"):
    """
    Tune hyperparameters, train and test a machine learning model.

    Parameters:
    df (pd.DataFrame): DataFrame containing the features and target.
    selected_features (list): The selected feature for model running.
    target (str): Name of the target variable.
    mlmodel (str): Machine learning model type ('MLR', 'DT', 'KNN', 'SVM', 'GBM', 'RF').

    Returns:
    dfe: DataFrame with specified columns, observed and predicted values, and errors.
    """
    start_time = time.time()

    # Model selection
    if mlmodel == "MLR":
        model = LinearRegression()
        grid = dict()
    elif mlmodel == "DT":
        model = DecisionTreeRegressor()
        grid = dict(max_depth=randint(2, 20), min_samples_leaf=randint(5, 100))
    elif mlmodel == "KNN":
        model = KNeighborsRegressor()
        grid = dict(
            leaf_size=randint(1, 50), n_neighbors=randint(1, 30), p=randint(1, 5)
        )
    elif mlmodel == "SVM":
        model = SVR()
        grid = dict(gamma=loguniform(1e-4, 1), C=loguniform(0.1, 100))
    elif mlmodel == "GBM":
        model = GradientBoostingRegressor()
        grid = dict(
            n_estimators=randint(1, 500),
            max_leaf_nodes=randint(2, 100),
            learning_rate=loguniform(0.01, 1),
        )
    elif mlmodel == "RF":
        model = RandomForestRegressor()
        grid = dict(n_estimators=randint(1, 500), max_leaf_nodes=randint(2, 100))
    elif mlmodel == "SVM_C":
        model = SVC()
        grid = dict(gamma=loguniform(1e-4, 1), C=loguniform(0.1, 100))
    elif mlmodel == "GBM_C":
        model = GradientBoostingClassifier()
        grid = dict(
            n_estimators=randint(1, 500),
            max_leaf_nodes=randint(2, 100),
            learning_rate=loguniform(0.01, 1),
        )
    elif mlmodel == "RF_C":
        model = RandomForestClassifier()
        grid = dict(n_estimators=randint(1, 500), max_leaf_nodes=randint(2, 100))
    else:
        raise ValueError("Please provide a valid ML model type!")

    # Prepare data
    X = MinMaxScaler().fit_transform(X)

    # 10-Fold Cross Validation
    if method == "k-fold":
        yhat, imps = kfold_cv(X, y, model, grid)

    elif method == "dataset":
        train_index = ~np.isnan(y)
        test_index = [True] * train_index.size
        yhat, imps = train_test(X, y, train_index, test_index, grid, model)
    else:
        return print("PROVIDE VALID METHOD")

    end_time = time.time()
    print(
        "\n"
        + mlmodel
        + " | Time to process: "
        + str((end_time - start_time) / 60)
        + " min \n"
    )

    return yhat, imps


def stats(s1, s2):
    rq75 = np.percentile(np.maximum(abs(s1 / s2), abs(s2 / s1)), 75)
    r2 = 1 - ((s2 - s1) ** 2).sum() / ((s2 - s2.mean()) ** 2).sum()  # == nash
    rmse = ((s1 - s2) ** 2).mean() ** 0.5
    bias = ((s1 - s2).sum()) / s2.sum() * 100
    return {"rq75": rq75, "r2": r2, "rmse": rmse, "bias": bias}


def accuracy(y, yhat):
    print(f"Accuracy: {accuracy_score(y, yhat)}")
    print("Classification Report:")
    print(classification_report(y, yhat))


def plot_results(
    yobs, ypred, imps, target, mlmodel, savefigs=False, folder="docs/figures/"
):
    """
    plot (bool): If True, plot feature importance and observed vs predicted values.
    savefigs (bool): If True, save figures plotted (plot must be True).
    """

    # Compute statistic metrics of the cross validations
    stats_cv = stats(ypred, yobs)

    # Feature Importance Visualization
    imps_mean = imps.median()
    imps_sortindex = imps_mean.argsort()[::-1]

    fig, ax = plt.subplots(1, 1, figsize=(6, 8))
    ax.boxplot(
        imps.iloc[:, imps_sortindex[::-1][-12:]],
        vert=False,
        showfliers=False,
        labels=imps.columns[imps_sortindex[::-1][-12:]],
    )
    ax.set_title(f"{mlmodel}, {target}")
    fig.tight_layout()
    plt.show()

    bias = stats_cv["bias"].round(2)
    rmse = stats_cv["rmse"].round(2)
    r2 = stats_cv["r2"].round(2)

    if savefigs:
        fig.savefig(folder + "permimp_" + target + "_" + mlmodel + ".png", dpi=300)

    # Observed vs Predicted Visualization
    fig, ax1 = plt.subplots(1, 1, dpi=300, figsize=(5, 5))
    ax1.scatter(yobs, ypred, s=5, alpha=0.5)
    ax1.plot([0, yobs.max()], [0, yobs.max()], "k--")
    ax1.set_xlabel(f"Obs {target} [cm]")
    ax1.set_ylabel(f"Pred {target} [cm]")
    ax1.set_title(f"{mlmodel} \n BIAS: {bias:.2f}, RMSE: {rmse:.2f}, R²: {r2:.2f}")
    cutlim = np.percentile(yobs, 100)
    ax1.set_xlim([0, cutlim])
    ax1.set_ylim([0, cutlim])
    ax1.set_aspect("equal", "box")
    fig.tight_layout()
    plt.show()

    if savefigs:
        fig.savefig(folder + "result_" + target + "_" + mlmodel + ".png", dpi=300)
