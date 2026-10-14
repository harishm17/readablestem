## 1.6 Absolute Value Functions

## Learning Objectives

In this section you will:

Graph an absolute value function.

Solve an absolute value equation.

Solve an absolute value inequality.

![](images/page_0_image_6.jpg)

Figure 1 Distances in deep space can be measured in all directions. As such, it is useful to consider distance in terms of absolute values. (credit: "s58y"/Flickr)

Until the 1920s, the so-called spiral nebulae were believed to be clouds of dust and gas in our own galaxy, some tens of thousands of light years away. Then, astronomer Edwin Hubble proved that these objects are galaxies in their own right, at distances of millions of light years. Today, astronomers can detect galaxies that are billions of light years away. Distances in the universe can be measured in all directions. As such, it is useful to consider distance as an absolute value function. In this section, we will investigate absolute value functions.

## Understanding Absolute Value

Recall that in its basic form $f ( x ) = | x |$ **,** the absolute value function, is one of our toolkit functions. The absolute value function is commonly thought of as providing the distance the number is from zero on a number line. Algebraically, for whatever the input value is, the output is the value without regard to sign.

## Absolute Value Function

The absolute value function can be defined as a piecewise function

$$
f ( x ) = | x | = \{ \begin{array} { r l r } { x } & { { } { \mathrm { i f ~ } } x \geq 0 } \\ { - x } & { { } { \mathrm { i f ~ } } x \leq 0 } \end{array}
$$

## Example 1

## Determine a Number within a Prescribed Distance

Describe all values 𝑥 within or including a distance of 4 from the number 5.

## Solution

We want the distance between 𝑥 and 5 to be less than or equal to 4. We can draw a number line, such as the one in Figure 2, to represent the condition to be satisfied.

![](images/page_1_image_8.jpg)

## Figure 2

The distance from 𝑥 to 5 can be represented using the absolute value as $| x - 5 |$ **.** We want the values of 𝑥 that satisfy the condition $| x - 5 | \leq 4 .$

## Analysis

Note that

$$
- 4 \leq x - 5
$$

$$
x - 5 \leq 4
$$

$$
1 \leq x
$$

$$
x \leq 9
$$

So $| x - 5 | \leq 4$ is equivalent to $1 \leq x \leq 9 .$

However, mathematicians generally prefer absolute value notation.

## Try It #1

Describe all values 𝑥 within a distance of 3 from the number 2.

## Example 2

## Resistance of a Resistor

Electrical parts, such as resistors and capacitors, come with specified values of their operating parameters: resistance, capacitance, etc. However, due to imprecision in manufacturing, the actual values of these parameters vary somewhat from piece to piece, even when they are supposed to be the same. The best that manufacturers can do is to try to guarantee that the variations will stay within a specified range, often **±1%, ±5%,** or **±10%.**

Suppose we have a resistor rated at 680 ohms, **±5% .** Use the absolute value function to express the range of possible values of the actual resistance.

## Solution

5% of 680 ohms is 34 ohms. The absolute value of the difference between the actual and nominal resistance should not exceed the stated variability, so, with the resistance 𝑅 in ohms,

$$
| R - 6 8 0 | \leq 3 4
$$

## Try It #2

Students who score within 20 points of 80 will pass a test. Write this as a distance from 80 using absolute value notation.

## Graphing an Absolute Value Function

The most significant feature of the absolute value graph is the corner point at which the graph changes direction. This point is shown at the origin in Figure 3.

![](images/page_3_image_0.jpg)

Figure 3

Figure 4 shows the graph of $y = 2 | x - 3 | + 4.$ The graph of $y = | x |$ has been shifted right 3 units, vertically stretched by a factor of 2, and shifted up 4 units. This means that the corner point is located at $( 3 , 4 )$ for this transformed function.

![](images/page_3_image_3.jpg)

Figure 4

## Writing an Equation for an Absolute Value Function

Write an equation for the function graphed in Figure 5.

![](images/page_4_image_3.jpg)

## Solution

The basic absolute value function changes direction at the origin, so this graph has been shifted to the right 3 units and down 2 units from the basic toolkit function. See Figure 6.

![](images/page_5_image_2.jpg)

Figure 6

We also notice that the graph appears vertically stretched, because the width of the final graph on a horizontal line is not equal to 2 times the vertical distance from the corner to this line, as it would be for an unstretched absolute value function. Instead, the width is equal to 1 times the vertical distance as shown in Figure 7.

![](images/page_6_image_0.jpg)

Figure 7

From this information we can write the equation

**𝑓(𝑥) = 2 | 𝑥 − 3 | − 2, treating the stretch as a vertical stretch, or** $f _ {   } ( x ) = \mid 2 ( x - 3 ) \mid - 2 ,$ **treating the stretch as a horizontal compression.**

## Analysis

Note that these equations are algebraically equivalent—the stretch for an absolute value function can be written interchangeably as a vertical or horizontal stretch or compression. Note also that if the vertical stretch factor is negative, there is also a reflection about the x-axis.

## Q&A

## If we couldn’t observe the stretch of the function from the graphs, could we algebraically determine it?

Yes. If we are unable to determine the stretch based on the width of the graph, we can solve for the stretch factor by putting in a known pair of values for 𝑥 and $f ( x )$

$$
f(x) = a \mid x - 3 \mid - 2
$$

Now substituting in the point (1, 2)

$$
2 = a \left| 1 - 3 \right| - 2
$$

$$
4 = 2 a
$$

$$
a = 2
$$

## Try It #3

Write the equation for the absolute value function that is horizontally shifted left 2 units, is vertically flipped, and vertically shifted up 3 units.

## Q&A

**Do the graphs of absolute value functions always intersect the vertical axis? The horizontal axis?** Yes, they always intersect the vertical axis. The graph of an absolute value function will intersect the vertical axis when the input is zero.

No, they do not always intersect the horizontal axis. The graph may or may not intersect the horizontal axis, depending on how the graph has been shifted and reflected. It is possible for the absolute value function to intersect the horizontal axis at zero, one, or two points (see Figure 8).

![](images/page_7_image_5.jpg)

Figure 8 (a) The absolute value function does not intersect the horizontal axis. (b) The absolute value function intersects the horizontal axis at one point. (c) The absolute value function intersects the horizontal axis at two points.

## Solving an Absolute Value Equation

Now that we can graph an absolute value function, we will learn how to solve an absolute value equation. To solve an equation such as $8 = \left| 2x - 6 \right|$ **,** we notice that the absolute value will be equal to 8 if the quantity inside the absolute value is 8 or -8. This leads to two different equations we can solve independently.

$$
\begin{array} { c c c } { { 2 x - 6 = 8 } } & { { \mathrm { o r } } } & { { 2 x - 6 = \; - \; 8 } } \\ { { 2 x = 1 4 } } & { { 2 x = \; - \; 2 } } \\ { { x = 7 } } & { { x = \; - \; 1 } } \end{array}
$$

Knowing how to solve problems involving absolute value functions is useful. For example, we may need to identify numbers or points on a line that are at a specified distance from a given reference point.

An absolute value equation is an equation in which the unknown variable appears in absolute value bars. For example,

$$
| x | = 4 ,
$$

$$
| 2 x - 1 | = 3
$$

$$
|5x + 2| - 4 = 9
$$

## Solutions to Absolute Value Equations

For real numbers 𝐴 and $B ,$ an equation of the form $| A | = B ,$ **,** with $B \geq 0 ,$ will have solutions when $A = B$ or $A = - B$ . If $B \leq 0 _ { i }$ , the equation $| A | = B$ has no solution.

## How To

**Given the formula for an absolute value function, find the horizontal intercepts of its graph**.

1. Isolate the absolute value term.

3. Solve for **𝑥 .**

2. Use $| A | = B$ to write $A = B \mathrm{or} - A = B,$ assuming $B \geq 0$

## Example 4

## Finding the Zeros of an Absolute Value Function

For the function $f(x) = \left| 4x + 1 \right| - 7,$ find the values of 𝑥 such that $f ( x ) = 0 .$

## Solution

<table><tbody><tr><td>0 = | 4𝑥 + 1 | - 7</td><td>Substitute 0 for f(x).</td></tr><tr><td>7 = | 4𝑥 + 1|</td><td>Isolate the absolute value on one side of the equation.</td></tr><tr><td rowspan="2">7 = 4𝑥 + 1 or -7 = 4𝑥 + 1 6 = 4𝑥 -8 = 4𝑥 𝑥 = 64 = 1.5 𝑥 = -48 = - 2</td><td rowspan="2">Break into two separate equations and solve.</td></tr><tr></tr></tbody></table>

The function outputs 0 when $x = 1.5   or   x = -2.$ See Figure 9.

![](images/page_9_image_6.jpg)

Figure 9

## Try It #4

For the function $f ( x ) = | 2 x - 1 | - 3 ,$ find the values of 𝑥 such that $f ( x ) = 0 .$

## Q&A

Should we always expect two answers when solving $| A | = B   ?$

No. We may find one, two, or even no answers. For example, there is no solution to $2+\left | 3x-5 \right | =1$

## How To

**Given an absolute value equation, solve it.**

1. Isolate the absolute value term.

2. Use $| A | = B$ to write $A = B   or   A = -B$

3. Solve for **𝑥 .**

## Example 5

## Solving an Absolute Value Equation

Solve **1 = 4|𝑥 − 2| + 2.**

## Solution

Isolating the absolute value on one side of the equation gives the following.

$$
1 = 4 \left| x - 2 \right| + 2
$$

$$
-1 = 4|x - 2|
$$

$$
- { \textstyle { \frac { 1 } { 4 } } } = | x - 2 |
$$

The absolute value always returns a positive value, so it is impossible for the absolute value to equal a negative value. At this point, we notice that this equation has no solutions.

**Q&A**

**In Example 5,** $\mathbf { i f }   f _ { \bar { } } ( x ) = 1$ **and** $g(x) = 4|x - 2| + 2$ **were graphed on the same set of axes, would the graphs intersect?**

No. The graphs of 𝑓 and 𝑔 would not intersect, as shown in Figure 10. This confirms, graphically, that the equation $1 = 4 | x - 2 | + 2$ has no solution.

![](images/page_11_chart_3.jpg)

Figure 10

**Try It #5**

Find where the graph of the function $f ( x ) = - | x + 2 | + 3$ intersects the horizontal and vertical axes.

## Solving an Absolute Value Inequality

Absolute value equations may not always involve equalities. Instead, we may need to solve an equation within a range of values. We would use an absolute value inequality to solve such an equation. An absolute value inequality is an equation of the form

$$
| A | \leq B , | A | \leq B , | A | > B , \quad \mathrm { o r } \quad | A | \geq B ,
$$

where an expression 𝐴 (and possibly but not usually 𝐵 ) depends on a variable **𝑥 .** Solving the inequality means finding the set of all 𝑥 that satisfy the inequality. Usually this set will be an interval or the union of two intervals.

There are two basic approaches to solving absolute value inequalities: graphical and algebraic. The advantage of the graphical approach is we can read the solution by interpreting the graphs of two functions. The advantage of the algebraic approach is it yields solutions that may be difficult to read from the graph.

For example, we know that all numbers within 200 units of 0 may be expressed as

$$
|x| < 200 \quad  or  \quad -200 < x < 200
$$

Suppose we want to know all possible returns on an investment if we could earn some amount of money within \$200 of \$600. We can solve algebraically for the set of values 𝑥 such that the distance between 𝑥 and 600 is less than 200. We represent the distance between 𝑥 and 600 as $| x - 6 0 0 |$

$$
|x - 600| < 200 \quad  or  \quad -200 < x - 600 < 200
$$

$$
-200+600<x-600+600<200+600
$$

$$
4 0 0 < x < 8 0 0
$$

This means our returns would be between \$400 and \$800.

Sometimes an absolute value inequality problem will be presented to us in terms of a shifted and/or stretched or compressed absolute value function, where we must determine for which values of the input the function’s output will be negative or positive.

## How To

**Given an absolute value inequality of the form** $| x - A | \leq B$ **for real numbers 𝑎 and 𝑏 where 𝑏 is positive, solve the absolute value inequality algebraically.**

1. Find boundary points by solving $|x - A| = B$

2. Test intervals created by the boundary points to determine where $| x - A | \leq B$

3. Write the interval or union of intervals satisfying the inequality in interval, inequality, or set-builder notation.

## Solving an Absolute Value Inequality

Solve **|𝑥 − 5 | < 4.**

## Solution

With both approaches, we will need to know first where the corresponding equality is true. In this case we first will find where $| x - 5 | = 4 .$ We do this because the absolute value is a function with no breaks, so the only way the function values can switch from being less than 4 to being greater than 4 is by passing through where the values equal 4. Solve $| x - 5 | = 4 .$

$$
\begin{aligned}x - 5 &= 4 \\x &= 9\end{aligned} \quad  or  \quad\begin{aligned}x - 5 &= - 4 \\x &= 1\end{aligned}
$$

After determining that the absolute value is equal to $4\mathrm{at}x = 1\mathrm{and}x = 9,$ we know the graph can change only from being less than 4 to greater than 4 at these values. This divides the number line up into three intervals:

$$
x < 1, \quad 1 < x < 9, \quad \mathrm{and} \quad x > 9.
$$

To determine when the function is less than 4, we could choose a value in each interval and see if the output is less than or greater than $^ { 4 , }$ as shown in Table 1.

<table><tbody><tr><td>Interval test𝑥</td><td>𝑓(𝑥)</td><td colspan="2">&lt; 4 or &gt; 4?</td></tr><tr><td>𝑥 &lt; 1</td><td>0</td><td>|0 - 5| = 5</td><td>Greater than</td></tr><tr><td>1 &lt;𝑥 &lt; 9</td><td>6</td><td>|6 - 5| = 1</td><td>Less than</td></tr><tr><td>𝑥 &gt; 9</td><td>11</td><td>|11 - 5| = 6</td><td>Greater than</td></tr></tbody></table>

Table 1

Because $1 < x < 9$ is the only interval in which the output at the test value is less than $^ { 4 , }$ we can conclude that the solution to $|x - 5| < 4   is   1 < x < 9,   or   (1, 9)$

To use a graph, we can sketch the function $f _ { } ( x ) = | x - 5 |$ **.** To help us see where the outputs are $^ { 4 , }$ the line $g ( x ) = 4$ could also be sketched as in Figure 11.

![](images/page_14_chart_10.jpg)

Figure 11 Graph to find the points satisfying an absolute value inequality.

We can see the following:

The output values of the absolute value are equal to 4 a $x = 1$ and $x = 9$

The graph of 𝑓 is below the graph of $^ { 1 g }$ on $1 < x < 9$ **.** This means the output values of $f ( x )$ are less than the output values of **𝑔(𝑥) .**

The absolute value is less than or equal to 4 between these two points, when $1 < x < 9 .$ **.** In interval notation, this would be the interval **(1, 9) .**

## Analysis

For absolute value inequalities,

$$
| x - A | < C ,
$$

$$
| x - A | > C ,
$$

$$
- C < x - A < C , \quad x - A < - C \quad \mathrm { o r } \quad x - A > C .
$$

The $< \mathrm { ~ o r ~ } ^ { > }$ symbol may be replaced by $\leq \textsf { o r } \geq$

So, for this example, we could use this alternative approach.

$$
\mid x - 5 \mid < 4
$$

$$
-4 < x - 5 < 4
$$

$$
-4+5<x-5+5<4+5
$$

$$
1 < x < 9
$$

**Rewrite by removing the absolute value bars.**

**Isolate the 𝑥 .**

## Try It #6

Solve $| x + 2 | \leq 6 .$

## How To

**Given an absolute value function, solve for the set of inputs where the output is positive (or negative).**

1. Set the function equal to zero, and solve for the boundary points of the solution set.

2. Use test points or a graph to determine where the function’s output is positive or negative.

## Example 7

## Using a Graphical Approach to Solve Absolute Value Inequalities

Given the function $f ( x ) = - { \frac { 1 } { 2 } } | 4 x - 5 | + 3 ,$ determine the **𝑥-** values for which the function values are negative.

## Solution

We are trying to determine where $f ( x ) < 0 ,$ which is when $-\frac{1}{2} \quad \left| 4x - 5 \right| + 3 < 0.$ **.** We begin by isolating the absolute value.

$$
-\frac{1}{2} \mid 4x - 5 \mid < -3
$$

**Multiply both sides by** –**2, and reverse the inequality.**

$$
| 4 x - 5 | > 6
$$

Next we solve for the equality $|4x - 5| = 6.$

$$
4x - 5 = 6 \quad  or  \quad 4x - 5 = -6
$$

$$
4x - 5 = 6
$$

$$
4 x = - 1
$$

$$
x = { \frac { 1 1 } { 4 } }
$$

$$
x = - { \frac { 1 } { 4 } }
$$

Now, we can examine the graph of 𝑓 to observe where the output is negative. We will observe where the branches are below the x-axis. Notice that it is not even important exactly what the graph looks like, as long as we know that it crosses the horizontal axis at $x = - \frac{1}{4} \quad  and  \quad x = \frac{11}{4}$ and that the graph has been reflected vertically. See Figure 12.

![](images/page_17_image_12.jpg)

Figure 12

We observe that the graph of the function is below the x-axis left of $x = - { \frac { 1 } { 4 } }$ and right of $x = { \frac { 1 1 } { 4 } }$ . This means the function values are negative to the left of the first horizontal intercept at $\begin{array} { r } { x = \; - \frac { 1 } { 4 } , } \end{array}$ and negative to the right of the second intercept at $x = { \frac { 1 1 } { 4 } }$ . This gives us the solution to the inequality.

$$
x < - \frac { 1 } { 4 } \quad \mathrm { o r } \quad x > \frac { 1 1 } { 4 }
$$

In interval notation, this would be $( - \infty ,   - 0 . 2 5 ) \cup ( 2 . 7 5 , \infty )$

## Try It #7

Solve **−2|𝑘 − 4| ≤ − 6.**

## Media

Access these online resources for additional instruction and practice with absolute value.

Graphing Absolute Value Functions

Graphing Absolute Value Functions 2

Equations of Absolute Value Function

Equations of Absolute Value Function 2

Solving Absolute Value Equations

## 1.6 Section Exercises

**Verbal**

1.

How do you solve an absolute value equation?

2.

How can you tell whether an absolute value function has two x-intercepts without graphing the function?

3.

When solving an absolute value function, the isolated absolute value term is equal to a negative number. What does that tell you about the graph of the absolute value function?

4.

How can you use the graph of an absolute value function to determine the x-values for which the function values are negative?

5.

How do you solve an absolute value inequality algebraically?

## Algebraic

6.

Describe all numbers 𝑥 that are at a distance of 4 from the number 8. Express this using absolute value notation.

7.

Describe all numbers 𝑥 that are at a distance of $\frac { 1 } { 2 }$ from the number −4. Express this using absolute value notation.

8.

Describe the situation in which the distance that point 𝑥 is from 10 is at least 15 units. Express this using absolute value notation.

9.

Find all function values **𝑓(𝑥)** such that the distance from **𝑓(𝑥)** to the value 8 is less than 0.03 units. Express this using absolute value notation.

For the following exercises, solve the equations below and express the answer using set notation.

```txt
10.
|𝑥 + 3 | = 9
```

11. **|6 − 𝑥 | = 5**

12. **|5𝑥 − 2 | = 11**

13. **|4𝑥 − 2 | = 11**

14. **2 | 4 − 𝑥 | = 7**

15. **3 | 5 − 𝑥 | = 5**

16. **3 | 𝑥 + 1 | − 4 = 5**

17. **5|𝑥 − 4| − 7 = 2**

18. **0 = − |𝑥 − 3| + 2**

```txt
19.
2|𝑥 − 3| + 1 = 2
```

$$
\begin{array} { l } 2 0 . \\ | 3 x - 2 | = 7 \end{array}
$$

```txt
21.
|3𝑥 − 2| = − 7
```

$$
\begin{array} { l } 2 2 . \\ | \frac { 1 } { 2 } x - 5 | = 1 1 \end{array}
$$

$$
\begin{array} { l } 2 3 . \\ \left| \frac { 1 } { 3 } x + 5 \right| = 1 4 \end{array}
$$

$$
\begin{array} { l } 2 4 . \\ - \left| \frac { 1 } { 3 } x + 5 \right| + 1 4 = 0 . \end{array}
$$

For the following exercises, find the x- and y-intercepts of the graphs of each function.

$$
\begin{aligned}25. \\f(x) = 2|x + 1| - 10\end{aligned}
$$

$$
\begin{aligned}26. \\f(x) = 4|x - 3| + 4\end{aligned}
$$

$$
\begin{aligned}27. \\f(x) = & -3|x - 2| - 1\end{aligned}
$$

$$
f(x) = - 2|x + 1| + 6
$$

For the following exercises, solve each inequality and write the solution in interval notation.

$$
\begin{array}{l}29.\\\left|x-2\right|>10.\end{array}
$$

$$
\begin{aligned} &30.\\ &2|v - 7| - 4 \geq 42.\\ \end{aligned}
$$

$$
\begin{array} { l } { 3 1 . } \\ { | 3 x - 4 | \leq 8 . } \end{array}
$$

$$
\begin{array} { l } 3 2 . \\ \left| x - 4 \right| \geq 8 \end{array}
$$

$$
\begin{array} { l } 3 3 . \\ | 3 x - 5 | \geq 1 3 \end{array}
$$

$$
\begin{array} { l } 3 4 . \\ | 3 x - 5 | \geq - 1 3 \end{array}
$$

```txt
35.
| 3 𝑥 − 5 | ≥ 7
```

```txt
36.
|3 𝑥 − 5| + 1 ≤ 16
```

## Graphical

For the following exercises, graph the absolute value function. Plot at least five points by hand for each graph.

```txt
37.
𝑦 = | 𝑥 − 1|
```

```txt
38.
𝑦 = | 𝑥 + 1|
```

```txt
39.
𝑦 = | 𝑥 | + 1
```

For the following exercises, graph the given functions by hand.

```txt
40.
𝑦 = |𝑥| − 2
```

```txt
41.
𝑦 = − |𝑥|
```

$$
\begin{cases}42. \\y = - \left | x \right |  - 2.\end{cases}
$$

$$
\begin{aligned}43. \\y = & - \left| x - 3 \right| - 2\end{aligned}
$$

$$
f(x) = - \left| x - 1 \right| - 2
$$

$$
\begin{aligned}45. \\f(x) = - \left| x + 3 \right| + 4.\end{aligned}
$$

$$
\begin{aligned}46. \\f(x) = 2 \mid x + 3 \mid + 1\end{aligned}
$$

$$
\begin{aligned}47. \\f(x) = 3|x - 2| + 3\end{aligned}
$$

$$
\begin{aligned}48. \\f(x) &= \left| 2x - 4 \right| - 3\end{aligned}
$$

$$
\begin{array} { l } 4 9 . \\ \mathit { f } ( x ) = | 3 x + 9 | + 2 . \end{array}
$$

$$
\begin{aligned}50. \\f(x) = - \left| x - 1 \right| - 3\end{aligned}
$$

$$
\begin{array} { l } { { 5 1 . } } \\ { { { \it f } ( x ) = - | x + 4 | - 3 . } } \end{array}
$$

$$
\begin{cases}52. \\f(x) = \frac{1}{2} \left| x + 4 \right| - 3\end{cases}
$$

## Technology

Use a graphing utility to graph **𝑓(𝑥) = 10 | 𝑥 − 2|** on the viewing window **[0, 4] .** Identify the corresponding range. Show the graph.

54.

Use a graphing utility to graph **𝑓(𝑥) = − 100 | 𝑥 | + 100** on the viewing window **[−5, 5] .** Identify the corresponding range. Show the graph.

For the following exercises, graph each function using a graphing utility. Specify the viewing window.

$$
f(x) = -0.1|0.1(0.2 - x)| + 0.3
$$

$$
f(x)=4 \times 10^{9} \left| x-(5 \times 10^{9}) \right| + 2 \times 10^{9}
$$

## Extensions

For the following exercises, solve the inequality.

57.

$$
| - 2x - \frac{2}{3}(x + 1) | + 3 > - 1
$$

58.

If possible, find all values of 𝑎 such that there are no **𝑥-** intercepts for **𝑓(𝑥) = 2|𝑥 + 1| + 𝑎 .**

59.

If possible, find all values of 𝑎 such that there are no 𝑦-intercepts for **𝑓(𝑥) = 2|𝑥 + 1| + 𝑎 .**

## Real-World Applications

60.

Cities A and B are on the same east-west line. Assume that city A is located at the origin. If the distance from city A to city B is at least 100 miles and 𝑥 represents the distance from city B to city A, express this using absolute value notation.

61.

The true proportion 𝑝 of people who give a favorable rating to Congress is 8% with a margin of error of 1.5%. Describe this statement using an absolute value equation.

62.

Students who score within 18 points of the number 82 will pass a particular test. Write this statement using absolute value notation and use the variable 𝑥 for the score.

63.

A machinist must produce a bearing that is within 0.01 inches of the correct diameter of 5.0 inches. Using 𝑥 as the diameter of the bearing, write this statement using absolute value notation.

64.

The tolerance for a ball bearing is 0.01. If the true diameter of the bearing is to be 2.0 inches and the measured value of the diameter is 𝑥 inches, express the tolerance using absolute value notation.

“1.6 Absolute Value Functions” from Precalculus by Jay Abramson, OpenStax.

Licensed under Creative Commons Attribution License (CC BY 4.0). [http://creativecommons.org/licenses/by/4.0/](http://creativecommons.org/licenses/by/4.0/)

Original page: [https://openstax.org/books/precalculus/pages/1-6-absolute-value-functions](https://openstax.org/books/precalculus/pages/1-6-absolute-value-functions)

Access for free at openstax.org.

Changes: reformatted as a printable page for evaluating a11y-stem; image alt text left out of this print; no wording edited.

a11y-stem is not affiliated with or endorsed by OpenStax.